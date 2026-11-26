import re
import unittest
from pathlib import Path

import yaml


class WrongNoteDevelopmentReleaseContract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_gate_is_after_blue_green_and_before_preprod(self):
        workflow = yaml.safe_load((self.root / ".github/workflows/v1-build-and-push-latest.yml").read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        steps = jobs["verify-api-development"]["steps"]
        deploy = next(i for i, s in enumerate(steps) if "./scripts/v1/deploy-api-development.ps1" in s.get("run", ""))
        smoke = next(i for i, s in enumerate(steps) if "./scripts/v1/run-wrong-note-development-smoke.ps1" in s.get("run", ""))
        self.assertGreater(smoke, deploy)
        self.assertIn("verify-api-development", jobs["verify-api-preprod"]["needs"])
        self.assertIn("wrong_note_smoke == 'true'", steps[smoke]["if"])
        self.assertIn("deployment_lock.py renew", steps[smoke]["run"])
        detect = next(s["run"] for s in jobs["detect-changes"]["steps"] if s.get("id") == "changes")
        pattern = re.search(r"if grep -qE '([^']+)' <<< \"\$CHANGED_RELEASE\"", detect)[1]
        for path in ("apps/domains/results/services/wrong_note_batch_service.py",
                     "academy/application/use_cases/ai/pipelines/paired_source_pipeline.py",
                     "academy/framework/workers/ai_sqs_worker.py", "apps/core/services/development_qa_queue_ack.py",
                     "apps/core/services/wrong_note_development_scenario.py"):
            self.assertRegex(path, pattern)
        self.assertNotRegex("apps/domains/attendance/views/calendar.py", pattern)


if __name__ == "__main__":
    unittest.main()
