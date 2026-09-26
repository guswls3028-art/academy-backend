"""Offline behavior tests: source boundaries, receipts, provenance, retention."""
from __future__ import annotations
import copy
import hashlib
import io
import zipfile
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import candidate_runtime as runtime
import candidate_manifest as manifest
import candidate_prepare as prepare
import candidate_slot as slot

spec = importlib.util.spec_from_file_location("candidate_ecr_cleanup", HERE / "ecr-cleanup.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
RELEASE = f"sha-{SHA}-run-123-1"


def values():
    base = {"AWS_DEFAULT_REGION": runtime.REGION, "DB_HOST": "isolated.db.internal",
            "DB_PORT": "5432", "DB_SSL_MODE": "require"}
    return {
        "api_base": base, "workers_base": dict(base),
        "db_credentials": {"DB_USER": "academy_api_development_app", "DB_PASSWORD": "x" * 40},
        "r2_credentials": {"R2_ENDPOINT": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
                           "R2_REGION": "auto", "R2_ACCESS_KEY": "a" * 20,
                           "R2_SECRET_KEY": "b" * 40, "R2_BUCKET": "academy-development-artifacts"},
    }


def candidate(mode="production"):
    now = int(time.time())
    return {
        "schemaVersion": 1, "status": "candidate", "complete": False,
        "gitSha": SHA, "releaseImageTag": RELEASE,
        "images": {r: {"repository": r if mode == "production" else r.replace("academy-", "academy-qa-", 1),
                       "digest": DIGEST, "tag": RELEASE, "source": "built"} for r in manifest.IMAGE_NAMES},
        "preparation": {"repository": manifest.REPOSITORY, "workflow": manifest.WORKFLOW,
                        "mode": mode, "ref": "refs/heads/main", "controllerSha": SHA,
                        "runId": 123, "runAttempt": 1, "createdAt": now,
                        "expiresAt": now + 30 * 86400, "developmentGate": "pass",
                        "cleanupZero": True, "providerQa": "unavailable", "models": runtime.MODEL_PINS},
    }


class RuntimeBoundaryTests(unittest.TestCase):
    def test_provider_free_success_and_no_production_sources(self):
        api, workers = runtime.assemble("development", RELEASE, values(), "postgres")
        self.assertEqual(api["DB_NAME"], "academy_api_development")
        self.assertEqual(workers["TOOLS_SQS_QUEUE_NAME"], "academy-v1-development-tools-queue")
        self.assertEqual(api["GEMINI_API_KEY"], "")
        self.assertEqual(workers["GEMINI_API_KEY"], "")
        self.assertEqual(api["CDN_HLS_SIGNING_SECRET"], workers["CDN_HLS_SIGNING_SECRET"])
        self.assertNotIn("gemini_key", runtime.source_names("development"))
        self.assertNotIn("/academy/api/env", runtime.source_names("development").values())

    def test_explicit_provider_only_in_worker(self):
        source = values()
        source["gemini_key"] = "provider-fixture-no-real-key"
        api, workers = runtime.assemble("development", RELEASE, source, "postgres", True)
        self.assertEqual(api["GEMINI_API_KEY"], "")
        self.assertEqual(workers["GEMINI_API_KEY"], source["gemini_key"])

    def test_forbidden_base_keys_fail_closed(self):
        for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "AWS_SECRET_ACCESS_KEY",
                    "DB_PASSWORD", "R2_ACCESS_KEY", "BILLING_KEY_ENCRYPTION_PRIMARY_KEY",
                    "DJANGO_SETTINGS_MODULE", "CDN_HLS_SIGNING_SECRET", "UNKNOWN"):
            with self.subTest(key=key):
                source = values()
                source["api_base"][key] = "untrusted"
                with self.assertRaises(runtime.BoundaryError):
                    runtime.assemble("development", RELEASE, source, "postgres")

    def test_output_boundaries(self):
        cases = [
            ("db_credentials", "DB_USER", "postgres"),
            ("db_credentials", "DB_PASSWORD", "short"),
            ("r2_credentials", "R2_BUCKET", "academy-video"),
            ("r2_credentials", "R2_ENDPOINT", "https://attacker.invalid"),
            ("api_base", "DB_SSL_MODE", "disable"),
            ("workers_base", "DB_HOST", "different.internal"),
        ]
        for section, key, value in cases:
            with self.subTest(key=key):
                source = values(); source[section][key] = value
                with self.assertRaises(runtime.BoundaryError):
                    runtime.assemble("development", RELEASE, source, "postgres")
        with self.assertRaises(runtime.BoundaryError):
            runtime.assemble("development", RELEASE, values(), "academy_api_development")
        with self.assertRaises(runtime.BoundaryError):
            runtime.source_names("preprod", True)

    def test_pinned_source_read_rejects_drift(self):
        ssm = Mock()
        names = runtime.source_names("development")
        versions = {k: 7 for k in names}
        def get(Name, WithDecryption):
            name = Name.rsplit(":", 1)[0]
            return {"Parameter": {"Name": name, "Version": 8, "Type": "SecureString", "Value": "{}"}}
        ssm.get_parameter.side_effect = get
        with self.assertRaises(runtime.BoundaryError):
            runtime.read_sources(ssm, "development", versions)
        self.assertTrue(ssm.get_parameter.call_args.kwargs["Name"].endswith(":7"))
        ssm.put_parameter.assert_not_called()

    def test_provider_metadata_optional_and_rotation_checked(self):
        ssm = Mock()
        ssm.describe_parameters.side_effect = lambda ParameterFilters: {"Parameters": [{
            "Name": ParameterFilters[0]["Values"][0], "Type": "SecureString", "Version": 3}]}
        tags = {"Environment": "development", "Owner": "fixture",
                "Purpose": "candidate-base-config", "SchemaVersion": "1"}
        def tag_response(ResourceType, ResourceId):
            current = dict(tags)
            if ResourceId.endswith("gemini-api-key"):
                current.update(Purpose="matchup-synthetic-qa", GoogleProjectId="fixture-project",
                               RotationOwner="fixture", RotateBy=str(date.today() - timedelta(days=1)))
            return {"TagList": [{"Key": k, "Value": v} for k, v in current.items()]}
        ssm.list_tags_for_resource.side_effect = tag_response
        self.assertNotIn("gemini_key", runtime.metadata(ssm, "development"))
        ssm.get_parameter.assert_not_called()
        with self.assertRaises(runtime.BoundaryError):
            runtime.metadata(ssm, "development", True)

    def test_rollback_rejects_foreign_newer_publication(self):
        ssm = Mock()
        receipt = {"stage": "development", "outputs": {
            "/academy/api/development/env": 10, "/academy/workers/development/env": 11},
            "previous_versions": {"/academy/api/development/env": 8,
                                  "/academy/workers/development/env": 9}}
        ssm.get_parameter.return_value = {"Parameter": {"Version": 12}}
        with self.assertRaises(runtime.BoundaryError):
            runtime.rollback(ssm, receipt)
        ssm.put_parameter.assert_not_called()



class PublicationTests(unittest.TestCase):
    def fake(self, fail_worker=False):
        source = values()
        names = runtime.source_names("development")
        history = {name: {1: json.dumps(source[kind])} for kind, name in names.items()}
        outputs = ("/academy/api/development/env", "/academy/workers/development/env")
        for name in outputs:
            history[name] = {1: "old-isolated-environment"}
        ssm = Mock()
        def describe(ParameterFilters):
            name = ParameterFilters[0]["Values"][0]
            return {"Parameters": [{"Name": name, "Type": "SecureString", "Version": max(history[name])}]}
        def get(Name, WithDecryption):
            if ":" in Name:
                name, suffix = Name.rsplit(":", 1); version = int(suffix)
            else:
                name = Name; version = max(history[name])
            return {"Parameter": {"Name": name, "Version": version, "Type": "SecureString", "Value": history[name][version]}}
        failures = {"remaining": int(fail_worker)}
        def put(Name, Type, Tier, Value, Overwrite):
            if Name == outputs[1] and failures["remaining"]:
                failures["remaining"] -= 1
                raise RuntimeError("injected worker publication failure")
            version = max(history[Name]) + 1
            history[Name][version] = Value
            return {"Version": version}
        ssm.describe_parameters.side_effect = describe
        ssm.get_parameter.side_effect = get
        ssm.put_parameter.side_effect = put
        ssm.list_tags_for_resource.return_value = {"TagList": [
            {"Key":"Environment","Value":"development"}, {"Key":"Owner","Value":"fixture"},
            {"Key":"Purpose","Value":"candidate-base-config"}, {"Key":"SchemaVersion","Value":"1"}]}
        return ssm, history, outputs

    def test_publish_and_exact_rollback_without_secret_receipt(self):
        ssm, history, outputs = self.fake()
        receipt = runtime.publish(ssm, "development", RELEASE, "postgres")
        self.assertEqual(receipt["outputs"], {name:2 for name in outputs})
        self.assertNotIn("x"*40, json.dumps(receipt))
        self.assertNotIn("b"*40, json.dumps(receipt))
        result = runtime.rollback(ssm, receipt)
        self.assertEqual(result["restored_versions"], {name:3 for name in outputs})
        for name in outputs:
            self.assertEqual(history[name][3], "old-isolated-environment")

    def test_partial_publication_restores_first_output(self):
        ssm, history, outputs = self.fake(fail_worker=True)
        with self.assertRaises(RuntimeError):
            runtime.publish(ssm, "development", RELEASE, "postgres")
        self.assertEqual(history[outputs[0]][max(history[outputs[0]])], "old-isolated-environment")
        self.assertEqual(max(history[outputs[1]]), 1)

    def test_invalid_base_does_not_write_outputs(self):
        ssm, history, outputs = self.fake()
        name = runtime.source_names("development")["api_base"]
        value = json.loads(history[name][1]);value["GEMINI_API_KEY"]="forbidden"
        history[name][1]=json.dumps(value)
        with self.assertRaises(runtime.BoundaryError):
            runtime.publish(ssm,"development",RELEASE,"postgres")
        ssm.put_parameter.assert_not_called()

class ProvenanceTests(unittest.TestCase):
    def test_production_success_and_qa_cannot_promote(self):
        manifest.validate(candidate(), sha=SHA)
        manifest.validate(candidate("isolated-qa"), promotion=False)
        with self.assertRaises(ValueError):
            manifest.validate(candidate("isolated-qa"), sha=SHA)

    def test_expiration_identity_and_missing_gate(self):
        cases = [
            ("expiresAt", int(time.time()) - 1), ("cleanupZero", False),
            ("developmentGate", "skipped"), ("controllerSha", "c" * 40),
            ("workflow", ".github/workflows/untrusted.yml"),
            ("models", {"text": "latest", "vision": "latest"}),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                doc = candidate(); doc["preparation"][key] = value
                with self.assertRaises(ValueError):
                    manifest.validate(doc, sha=SHA)
        with self.assertRaises(ValueError):
            manifest.validate(candidate(), sha="d" * 40)

    def test_exact_image_set_repository_and_digest(self):
        for mutate in (
            lambda d: d["images"].pop("academy-base"),
            lambda d: d["images"]["academy-api"].update(digest="latest"),
            lambda d: d["images"]["academy-api"].update(repository="academy-qa-api"),
            lambda d: d.update(releaseImageTag="sha-" + SHA + "-run-124-1"),
        ):
            doc = candidate(); mutate(doc)
            with self.assertRaises(ValueError):
                manifest.validate(doc)

    def test_source_gate_requires_main_controller_and_same_repo_pr(self):
        main = {"object": {"sha": SHA}}
        with patch.object(prepare, "github", return_value=main):
            prepare.resolve("production", SHA, SHA, "")
            with self.assertRaises(ValueError):
                prepare.resolve("production", "b" * 40, SHA, "")
            with self.assertRaises(ValueError):
                prepare.resolve("production", SHA, "c" * 40, "")
        pr = {"state": "open", "base": {"ref": "main"},
              "head": {"sha": "b" * 40, "repo": {"full_name": manifest.REPOSITORY}}}
        with patch.object(prepare, "github", side_effect=[main, pr]):
            prepare.resolve("isolated-qa", "b" * 40, SHA, "509")
        pr["head"]["repo"]["full_name"] = "foreign/fork"
        with patch.object(prepare, "github", side_effect=[main, pr]):
            with self.assertRaises(ValueError):
                prepare.resolve("isolated-qa", "b" * 40, SHA, "509")

    def test_restore_uses_exact_run_attempt_and_archive_digest(self):
        doc=candidate()
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,"w") as z:
            z.writestr("release-manifest.candidate.json",json.dumps(doc))
        raw=stream.getvalue(); digest="sha256:"+hashlib.sha256(raw).hexdigest()
        info={"name":"academy-candidate-123-1","expired":False,"digest":digest,
              "size_in_bytes":len(raw),"workflow_run":{"id":123}}
        run={"path":manifest.WORKFLOW,"event":"workflow_dispatch","head_branch":"main",
             "head_sha":SHA,"conclusion":"success","status":"completed","run_attempt":1,
             "repository":{"full_name":manifest.REPOSITORY}}
        def response(path):
            if path.endswith("git/ref/heads/main"):return {"object":{"sha":SHA}}
            if path.endswith("actions/artifacts/456"):return info
            self.assertTrue(path.endswith("actions/runs/123/attempts/1"))
            return run
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"candidate.json"
            with patch.object(manifest,"github",side_effect=response), patch.object(manifest,"archive",return_value=raw), patch.object(manifest,"verify_image_readback") as image_check:
                manifest.restore(456,digest,SHA,out)
                self.assertEqual(json.loads(out.read_text())["gitSha"],SHA)
                image_check.assert_called_once()
            with patch.object(manifest,"github",side_effect=response), patch.object(manifest,"archive",return_value=raw+b"tamper"), patch.object(manifest,"verify_image_readback") as image_check:
                with self.assertRaises(ValueError):
                    manifest.restore(456,digest,SHA,Path(temp)/"tampered.json")
                image_check.assert_not_called()
                self.assertFalse((Path(temp)/"tampered.json").exists())

    def test_create_requires_digest_bound_cleanup_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); images = candidate()["images"]
            (root/"images.json").write_text(json.dumps(images))
            evidence = {"release_id": RELEASE, "stage": "development", "syntheticSmokeCleanupZero": True}
            for key, role in (("api","academy-api"), ("tools","academy-tools-worker"),
                              ("ai","academy-ai-worker-cpu"), ("messaging","academy-messaging-worker")):
                evidence[key] = prepare.REGISTRY + "/" + role + "@" + DIGEST
            (root/"evidence.json").write_text(json.dumps(evidence))
            manifest.create(root/"images.json","production",SHA,SHA,123,1,root/"out.json",root/"evidence.json")
            evidence["api"] = prepare.REGISTRY + "/academy-api@sha256:" + "c"*64
            (root/"evidence.json").write_text(json.dumps(evidence))
            with self.assertRaises(ValueError):
                manifest.create(root/"images.json","production",SHA,SHA,123,1,root/"bad.json",root/"evidence.json")
            self.assertFalse((root/"bad.json").exists())


class RetentionTests(unittest.TestCase):
    def test_old_sha_candidate_survives_keep_zero(self):
        image = {"imageDigest": DIGEST, "imageTags": ["sha-old", f"candidate-until-{int(time.time())+3600}-123-1"],
                 "imageManifestMediaType": cleanup.TYPE_MANIFEST}
        protected, deletable = cleanup.identify_protected_set([image], 0, "academy-api")
        self.assertIn(DIGEST, protected)
        self.assertEqual(cleanup.classify_deletable([image], protected, deletable), ([], []))

    def test_expired_and_malformed_retention(self):
        self.assertFalse(cleanup.candidate_retained({"imageTags":["candidate-until-1-123-1"]}, now=2))
        with self.assertRaises(RuntimeError):
            cleanup.candidate_retained({"imageTags":["candidate-until-invalid"]})

    def test_manifest_failure_blocks_deletion(self):
        for response in ({"failures":[{"failureCode":"Missing"}]},
                         {"images":[{"imageId":{"imageDigest":DIGEST},"imageManifest":"not-json"}]},
                         {"images":[{"imageId":{"imageDigest":DIGEST},"imageManifest":"{}"}]}):
            with patch.object(cleanup,"aws_ecr",return_value=json.dumps(response)):
                with self.assertRaises((RuntimeError, ValueError)):
                    cleanup.resolve_child_digests("academy-api", DIGEST)



class SlotLifecycleTests(unittest.TestCase):
    def baseline_instance(self):
        fields = {"Lifecycle":"active","ReleaseId":RELEASE,
                  "ApiEnvVersion":"8","WorkersEnvVersion":"9","ProductionDatabase":"postgres"}
        fields.update({key:prepare.REGISTRY+"/academy-"+role+"@"+DIGEST for key,role in slot.ROLES.items()})
        return {"InstanceId":"i-0123456789abcdef0","State":{"Name":"running"},
                "ImageId":"ami-0885e191a9bcf28b0","InstanceType":"t4g.medium",
                "IamInstanceProfile":{"Arn":"arn:aws:iam::809466760795:instance-profile/academy-api-development"},
                "Tags":[{"Key":k,"Value":v} for k,v in fields.items()]}

    def fake_aws(self, current, sessions=False, busy=False, leftovers=False):
        def response(service,*args):
            if service=="ec2":
                if any("SlotLeaseOwner" in str(a) for a in args):
                    return {"Reservations":[{"Instances":current if leftovers else []}]}
                return {"Reservations":[{"Instances":current}]}
            if service=="ssm":
                return {"Sessions":[{"SessionId":"active-fixture"}] if sessions else []}
            if args[0]=="get-queue-url":
                return {"QueueUrl":f"https://sqs.{slot.REGION}.amazonaws.com/{slot.ACCOUNT}/"+args[2]}
            return {"Attributes":{"ApproximateNumberOfMessages":"1" if busy else "0",
                                  "ApproximateNumberOfMessagesNotVisible":"0",
                                  "ApproximateNumberOfMessagesDelayed":"0"}}
        return response

    def test_capture_idle_baseline_and_untouched_failure_restoration(self):
        original=self.baseline_instance()
        with patch.object(slot,"aws",side_effect=self.fake_aws([original])):
            snapshot=slot.capture("task-fixture","candidate:123:1")
            self.assertEqual(snapshot["images"]["ApiImageUri"],prepare.REGISTRY+"/academy-api@"+DIGEST)
            self.assertFalse(slot.assess(snapshot,require_restored=True)["replace"])

    def test_busy_owner_session_or_queue_blocks_before_qa(self):
        original=self.baseline_instance()
        for option in ("sessions","busy"):
            with self.subTest(option=option), patch.object(slot,"aws",side_effect=self.fake_aws([original],**{option:True})):
                with self.assertRaises(ValueError):
                    slot.capture("task-fixture","candidate:123:1")
        original["Tags"].append({"Key":"SlotLeaseOwner","Value":"other-task"})
        with patch.object(slot,"aws",side_effect=self.fake_aws([original])):
            with self.assertRaises(ValueError):
                slot.capture("task-fixture","candidate:123:1")

    def test_owned_qa_requires_restore_then_exact_cleanup_readback(self):
        original=self.baseline_instance()
        with patch.object(slot,"aws",side_effect=self.fake_aws([original])):
            snapshot=slot.capture("task-fixture","candidate:123:1")
        qa=copy.deepcopy(original)
        qa["InstanceId"]="i-0fedcba9876543210"
        qa["IamInstanceProfile"]["Arn"]="arn:aws:iam::809466760795:instance-profile/academy-api-qa"
        qa["Tags"] += [{"Key":"SlotLeaseOwner","Value":"task-fixture"},{"Key":"QaMode","Value":"isolated-qa"}]
        with patch.object(slot,"aws",side_effect=self.fake_aws([qa])):
            self.assertTrue(slot.assess(snapshot)["replace"])
            with self.assertRaises(ValueError):
                slot.assess(snapshot,require_restored=True)
        with patch.object(slot,"aws",side_effect=self.fake_aws([original],leftovers=True)):
            with self.assertRaises(ValueError):
                slot.assess(snapshot,require_restored=True)
        with patch.object(slot,"aws",side_effect=self.fake_aws([original])):
            self.assertTrue(slot.assess(snapshot,require_restored=True)["restored"])

    def test_missing_baseline_coordinates_or_unconfirmed_capacity_blocks(self):
        original=self.baseline_instance()
        original["Tags"]=[t for t in original["Tags"] if t["Key"]!="ApiEnvVersion"]
        with patch.object(slot,"aws",side_effect=self.fake_aws([original])):
            with self.assertRaises(ValueError):
                slot.capture("task-fixture","candidate:123:1")
        with patch.object(slot,"aws",side_effect=self.fake_aws([self.baseline_instance(),self.baseline_instance()])):
            with self.assertRaises(ValueError):
                slot.guard("task-fixture",require_baseline=True)

class PolicyAndWorkflowTests(unittest.TestCase):
    def test_qa_has_no_production_image_or_parameter_allow(self):
        policy = json.loads((HERE/"templates/iam/policy_candidate_qa.json").read_text())
        for statement in policy["Statement"]:
            if statement["Effect"] != "Allow":
                continue
            resources = statement["Resource"]
            resources = [resources] if isinstance(resources,str) else resources
            for resource in resources:
                if ":repository/" in resource:
                    self.assertIn(":repository/academy-qa-",resource)
                self.assertNotIn(":parameter/academy/api/env",resource)
                self.assertNotIn(":parameter/academy/workers/env",resource)
        lock = next(s for s in policy["Statement"] if s["Sid"] == "SharedDeploymentLock")
        self.assertEqual(lock["Condition"]["ForAllValues:StringEquals"]["dynamodb:LeadingKeys"],
                         ["__deployment_control_v2__"])

    def test_qa_cannot_relabel_or_command_previous_privileged_host(self):
        policy=json.loads((HERE/"templates/iam/policy_candidate_qa.json").read_text())
        statements={s["Sid"]:s for s in policy["Statement"]}
        self.assertEqual(statements["DevelopmentSsmSend"]["Condition"]["StringEquals"]["ssm:resourceTag/QaMode"],"isolated-qa")
        self.assertEqual(statements["DevelopmentLifecycleTags"]["Condition"]["ForAllValues:StringEquals"]["aws:TagKeys"],
                         ["Lifecycle","VerifiedReleaseId"])
        self.assertNotIn("ssm:SendCommand",statements["DevelopmentLifecycle"]["Action"])
        self.assertEqual(statements["DevelopmentPassRole"]["Resource"],
                         "arn:aws:iam::809466760795:role/academy-api-qa-role")

    def test_exact_environment_oidc(self):
        for mode in ("qa","production"):
            trust=json.loads((HERE/f"templates/iam/trust_candidate_{mode}.json").read_text())
            condition=trust["Statement"][0]["Condition"]["StringEquals"]
            self.assertEqual(condition["token.actions.githubusercontent.com:sub"],
                             f"repo:{manifest.REPOSITORY}:environment:candidate-{mode}")

    def test_workflow_yaml_and_legacy_publisher_branch(self):
        import yaml
        root=HERE.parents[1]
        for name in ("candidate-prepare.yml","v1-build-and-push-latest.yml"):
            data=yaml.safe_load((root/".github/workflows"/name).read_text())
            self.assertTrue(data["jobs"])
        source=(root/".github/workflows/v1-build-and-push-latest.yml").read_text()
        self.assertIn('if ($env:CANDIDATE_ARTIFACT_ID)',source)
        self.assertIn('./scripts/v1/publish-api-development-env.ps1',source)
        self.assertIn('./scripts/v1/publish-api-preprod-env.ps1',source)


if __name__ == "__main__":
    unittest.main()
