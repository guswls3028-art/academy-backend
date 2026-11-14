"""Execute the development-only storage builder/SSOT contract without AWS."""

import copy
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
STORAGE = ROOT / "scripts/v1/core/development_storage.ps1"
SSOT = ROOT / "scripts/v1/core/ssot.ps1"


def run_powershell(source, payload):
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", source],
        input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8",
        check=False,
    )


class DevelopmentStorageTests(unittest.TestCase):
    def setUp(self):
        self.image = {
            "RootDeviceType": "ebs", "RootDeviceName": "/dev/xvda",
            "BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {
                "VolumeSize": 8, "VolumeType": "gp3", "Iops": 3000,
                "Throughput": 125, "Encrypted": False, "SnapshotId": "snap-test",
            }}],
        }

    def build(self, image=None, size=20):
        helper = str(STORAGE).replace("'", "''")
        return run_powershell(
            f"$ErrorActionPreference='Stop'; . '{helper}'; "
            "$p=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
            "$mapping=Get-ApiDevelopmentRootBlockDeviceMapping -Image $p.image -VolumeSizeGb $p.size; "
            "ConvertTo-Json -InputObject @($mapping) -Depth 5 -Compress",
            {"image": image or self.image, "size": size},
        )

    def test_only_root_size_and_cleanup_override_ami_defaults(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted):
                self.image["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] = encrypted
                self.image["BlockDeviceMappings"].append({"DeviceName": "/dev/sdf", "VirtualName": "ephemeral0"})
                result = self.build()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), [{
                    "DeviceName": "/dev/xvda",
                    "Ebs": {"VolumeSize": 20, "DeleteOnTermination": True},
                }])

    def test_refuses_non_ebs_missing_ambiguous_and_oversized_ami_root(self):
        cases = []
        for key, value in (("RootDeviceType", "instance-store"), ("RootDeviceName", ""), ("BlockDeviceMappings", [])):
            image = copy.deepcopy(self.image)
            image[key] = value
            cases.append(image)
        duplicate = copy.deepcopy(self.image)
        duplicate["BlockDeviceMappings"] *= 2
        cases.append(duplicate)
        oversized = copy.deepcopy(self.image)
        oversized["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] = 32
        cases.append(oversized)
        for image in cases:
            with self.subTest(image=image):
                self.assertNotEqual(self.build(image).returncode, 0)

    def test_ssot_validates_size_before_deployment(self):
        helper = str(SSOT).replace("'", "''")
        source = (
            f"$ErrorActionPreference='Stop'; . '{helper}'; "
            "$testParams=Get-ParamsYaml; "
            "$testParams['apiDevelopment']['rootVolumeSizeGb']=[Console]::In.ReadToEnd() | ConvertFrom-Json; "
            "function Get-ParamsYaml { return $testParams }; "
            "Load-SSOT | Out-Null; $script:ApiDevelopmentRootVolumeSizeGb"
        )
        for size in (16, 20, 1024):
            result = run_powershell(source, size)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(size))
        for size in (None, "bad", 15, 1025, 20.5):
            with self.subTest(size=size):
                result = run_powershell(source, size)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("apiDevelopment.rootVolumeSizeGb", result.stderr)

    def test_payload_is_used_only_for_candidate_launch_and_iam_does_not_expand(self):
        deploy = (ROOT / "scripts/v1/deploy-api-development.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"--block-device-mappings", $developmentBlockRef', deploy)
        self.assertIn('"ec2", "describe-images", "--image-ids", $ami', deploy)
        self.assertIn("Remove-TempFiles @($networkFile, $developmentBlockFile)", deploy)
        self.assertNotIn('"modify-volume', deploy)
        policy = json.loads((ROOT / "infra/worker_asg/iam_policy_gha_development_deploy.json").read_text())
        launch = next(item for item in policy["Statement"] if item["Sid"] == "DevelopmentRunInstances")
        self.assertEqual(launch["Action"], "ec2:RunInstances")
        self.assertTrue(any(resource.endswith(":volume/*") for resource in launch["Resource"]))
        self.assertNotIn("Condition", launch)


if __name__ == "__main__":
    unittest.main()
