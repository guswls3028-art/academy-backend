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
        for entry, end in (('if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then', "exit 0"),
                           ("force_full_build() {", "exit 0")):
            branch = detect.split(entry, 1)[1].split(end, 1)[0]
            self.assertIn('echo "wrong_note_smoke=true"', branch)
            self.assertIn('echo "build_tools=true"', branch)


if __name__ == "__main__":
    unittest.main()
