from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts/operations/agent_session_snapshot.py"
spec = importlib.util.spec_from_file_location("agent_snapshot", SOURCE)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "academy"
        self.workspace.mkdir()
        self.codex = self.root / ".codex"
        (self.codex / "sessions").mkdir(parents=True)
        self.claude = self.root / ".claude"
        self.claude.mkdir()
        collector._TAIL_CACHE.clear()

    def log(self, path, event):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "event_msg", "timestamp": datetime.now(timezone.utc).isoformat(), "payload": {"type": event, "last_agent_message": "PRIVATE BODY"}}) + "\n", encoding="utf-8")

    def database(self):
        db = sqlite3.connect(self.codex / "state_5.sqlite")
        db.executescript("CREATE TABLE threads(id TEXT,name TEXT,agent_nickname TEXT,agent_role TEXT,rollout_path TEXT,cwd TEXT,updated_at INTEGER,archived INTEGER,first_user_message TEXT); CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT,status TEXT);")
        return db

    def test_scoped_names_and_real_edges_without_prompt_export(self):
        db = self.database()
        for node_id, cwd in [("root", self.workspace), ("child", self.workspace), ("forbidden", self.workspace / "dnfm"), ("sibling", self.root / "academy-other")]:
            log = self.codex / "sessions" / f"{node_id}.jsonl"
            self.log(log, "task_complete" if node_id == "child" else "task_started")
            db.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", (node_id, node_id, "", "reviewer", str(log), str(cwd), 1794729000, 0, "PRIVATE PROMPT"))
        db.execute("INSERT INTO thread_spawn_edges VALUES('root','child','open')")
        db.commit()
        db.close()
        nodes = collector.codex_nodes(self.codex, self.workspace)
        self.assertEqual({node["id"] for node in nodes}, {"codex:root", "codex:child"})
        child = next(node for node in nodes if node["id"] == "codex:child")
        self.assertEqual(child["parent_id"], "codex:root")
        self.assertEqual(child["status"], "completed")
        self.assertNotIn("PRIVATE", json.dumps(nodes))

    def test_arbitrary_rollout_path_is_not_read(self):
        db = self.database()
        outside = self.root / "outside.jsonl"
        self.log(outside, "task_complete")
        db.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", ("root", "title", "", "", str(outside), str(self.workspace), 1794729000, 0, ""))
        db.commit()
        db.close()
        self.assertEqual(collector.codex_nodes(self.codex, self.workspace)[0]["status"], "unknown")

    def test_partial_writes_and_cached_old_running_event_are_not_false_success(self):
        path = self.codex / "sessions" / "live.jsonl"
        self.log(path, "task_started")
        with path.open("ab") as stream:
            stream.write(b'{"type":"event_msg","payload":')
        self.assertEqual(collector.metadata_tail(path, "codex")["status"], "running")
        with patch.object(collector.time, "time", return_value=path.stat().st_mtime + 1801):
            self.assertEqual(collector.metadata_tail(path, "codex")["status"], "unknown")

    def test_claude_live_identity_overrides_old_transcript_and_dead_peer_is_unknown(self):
        project = self.claude / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(self.workspace.resolve()))
        project.mkdir(parents=True)
        (project / "session.jsonl").write_text(json.dumps({"type": "assistant", "message": {"stop_reason": "end_turn", "content": "PRIVATE BODY"}}) + "\n", encoding="utf-8")
        peers = self.claude / "sessions"
        peers.mkdir()
        peer = peers / "123.json"
        peer.write_text(json.dumps({"sessionId": "session", "cwd": str(self.workspace), "name": "Claude review", "status": "idle", "pid": 123, "procStart": "1", "updatedAt": 1794729000000}), encoding="utf-8")
        os.utime(peer, (1, 1))
        with patch.object(collector, "peer_is_alive", return_value=True):
            self.assertEqual(collector.claude_nodes(self.claude, self.workspace)[0]["status"], "waiting")
        with patch.object(collector, "peer_is_alive", return_value=False):
            self.assertEqual(collector.claude_nodes(self.claude, self.workspace)[0]["status"], "unknown")

    def test_unavailable_source_is_distinguished_from_zero_sessions(self):
        data = collector.collect(self.root / "missing-codex", self.root / "missing-claude", self.workspace)
        self.assertEqual(data["nodes"], [])
        self.assertEqual(data["source_errors"], ["codex", "claude"])

    def test_bad_registry_records_preserve_both_providers_and_report_partial_recovery(self):
        log = self.codex / "sessions" / "root.jsonl"
        self.log(log, "task_complete")
        db = self.database()
        db.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", ("root", "Codex", "", "", str(log), str(self.workspace), 1794729000, 0, ""))
        db.commit()
        db.close()
        peers = self.claude / "sessions"
        peers.mkdir()
        good = {"sessionId": "healthy", "cwd": str(self.workspace), "name": "Claude", "status": "idle", "pid": 123, "procStart": "1", "updatedAt": 1794729000000}
        (peers / "healthy.json").write_text(json.dumps(good), encoding="utf-8")
        bad = peers / "bad.json"
        malformed = [None, [], 42, "record", {}, {**good, "cwd": []}, {**good, "sessionId": []}, {**good, "status": []}, {**good, "name": {}}, {**good, "pid": True}, {**good, "updatedAt": float("inf")}, {**good, "updatedAt": 10**1000}]
        with patch.object(collector, "peer_is_alive", return_value=True):
            for value in malformed:
                with self.subTest(value_type=type(value).__name__):
                    bad.write_text(json.dumps(value), encoding="utf-8")
                    data = collector.collect(self.codex, self.claude, self.workspace)
                    self.assertEqual({node["id"] for node in data["nodes"]}, {"codex:root", "claude:healthy"})
                    self.assertEqual(data["source_errors"], ["claude"])
            for encoded in (b'{"sessionId":', b"x" * 32_769):
                bad.write_bytes(encoded)
                data = collector.collect(self.codex, self.claude, self.workspace)
                self.assertEqual(len(data["nodes"]), 2)
                self.assertEqual(data["source_errors"], ["claude"])
            bad.unlink()
            data = collector.collect(self.codex, self.claude, self.workspace)
            self.assertEqual(len(data["nodes"]), 2)
            self.assertEqual(data["source_errors"], [])

    def test_unexpected_log_value_types_do_not_abort_other_lifecycle_records(self):
        path = self.codex / "sessions" / "root.jsonl"
        self.log(path, "task_complete")
        with path.open("a", encoding="utf-8") as stream:
            stream.write('\nnull\n{"type":"event_msg","payload":{"type":["task_started"]}}\n')
            stream.write('{"type":"assistant","message":{"stop_reason":[]}}\n')
        self.assertEqual(collector.metadata_tail(path, "codex")["status"], "completed")
        collector._TAIL_CACHE.clear()
        self.assertEqual(collector.metadata_tail(path, "claude")["status"], "completed")

    def test_hidden_publisher_writes_only_sanitized_atomic_health_receipt(self):
        receipt = self.root / "state.json"
        with patch.object(collector.sys, "stdout", None):
            collector.report({"status": "unavailable", "error_class": "ClientError"}, receipt)
        result = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("nodes", result)
        self.assertFalse(receipt.with_suffix(".tmp").exists())

    def test_publisher_uses_api_storage_default_but_refuses_other_targets(self):
        values = {"R2_ENDPOINT": f"https://{collector.R2_ACCOUNT}.r2.cloudflarestorage.com", "R2_ACCESS_KEY": "test", "R2_SECRET_KEY": "test"}
        sts, ssm = Mock(), Mock()
        sts.get_caller_identity.return_value = {"Account": collector.AWS_ACCOUNT}
        with patch("boto3.Session") as session, patch("boto3.client") as client:
            session.return_value.client.side_effect = lambda name, **kwargs: {"sts": sts, "ssm": ssm}[name]
            ssm.get_parameter.return_value = {"Parameter": {"Value": json.dumps(values)}}
            self.assertIs(collector.publishing_client(), client.return_value)
            values["R2_STORAGE_BUCKET"] = "another-bucket"
            ssm.get_parameter.return_value = {"Parameter": {"Value": json.dumps(values)}}
            with self.assertRaises(ValueError):
                collector.publishing_client()


if __name__ == "__main__":
    unittest.main()
