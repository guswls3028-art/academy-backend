"""Offline launch/boot boundary tests. No AWS, Docker or credential calls."""
from __future__ import annotations
import ast
import base64
import copy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/v1"))
import candidate_qa_launch as launch
import candidate_qa_window as window


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.record = dict(window.specification(
            lease_id="a"*32, owner_task="01a0d04a-d64f-7473-9f58-61a8e983dcc0",
            lock_owner="candidate:123:1", source_sha="b"*40,
            images={k:"sha256:"+"c"*64 for k in launch.ROLE_NAMES},
            endpoint="ssm://i-0123456789abcdef0:8000", profile=window.PROFILE,
            scope=[509,511], baseline_sha256="d"*64, tenant_ids=[101], message_key_version=1),
            state="prepared", control_hold=False, revision=1, started_at=1000,
            renewed_at=1000, expires_at=2000)
        self.release="sha-"+"b"*40+"-run-123-1"
        self.store=Mock()
        self.store.read.side_effect=lambda:copy.deepcopy(self.record)
        self.store.table="academy-v1-video-job-lock"
        self.store.client.get_item.return_value={"Item":{"owner":{"S":"candidate:123:1"},"ttl":{"N":"3000"}}}
        self.ec2=Mock();self.ssm=Mock();self.sts=Mock()
        self.sts.get_caller_identity.return_value={
            "Account":launch.ACCOUNT,
            "Arn":"arn:aws:sts::"+launch.ACCOUNT+":assumed-role/academy-gha-candidate-production/test"}
        self.launcher=launch.InertLauncher(ec2=self.ec2,ssm=self.ssm,sts=self.sts,store=self.store,clock=lambda:1100)
        self.instance={"InstanceId":"i-0123456789abcdef0","State":{"Name":"running"},
            "IamInstanceProfile":{"Arn":window.PROFILE},"Tags":[
                {"Key":k,"Value":v} for k,v in {
                    "QaMode":"isolated-qa","SlotLeaseOwner":self.record["owner_task"],
                    "CandidateLeaseId":self.record["lease_id"],"CandidateSourceSha":self.record["source_sha"],
                    "ReleaseId":self.release}.items()]}
        self.ec2.describe_instances.return_value={"Reservations":[{"Instances":[self.instance]}]}
        self.ssm.send_command.return_value={"Command":{"CommandId":"command"}}
        self.controller=patch.object(launch,"assert_main_controller",return_value="e"*40)
        self.controller.start();self.addCleanup(self.controller.stop)

    def plan(self):
        return launch.start_plan(self.record,release_id=self.release,api_version=7,workers_version=8)

    def test_stage_one_never_starts_product_or_fetches_environment(self):
        script=launch.INERT_USER_DATA
        self.assertIn("CANDIDATE_LEASE_REQUIRED=true",script)
        self.assertIn("ACADEMY_QA_MODE=isolated-qa",script)
        for forbidden in ("docker run","get-parameter","with-decryption","migrate","receive-message","GEMINI"):
            self.assertNotIn(forbidden,script)
        self.assertIn('test -z "$(docker ps -aq)"',script)

    def test_plan_rejects_phase_hold_wrong_attempt_and_environment_versions(self):
        for key,value in (("state","active"),("control_hold",True)):
            old=self.record[key];self.record[key]=value
            with self.assertRaises(window.WindowHold):self.plan()
            self.record[key]=old
        for kwargs in (
            dict(release_id=self.release.replace("-123-1","-123-2"),api_version=7,workers_version=8),
            dict(release_id=self.release,api_version=True,workers_version=8),
        ):
            with self.assertRaises(window.WindowHold):launch.start_plan(self.record,**kwargs)

    def test_start_binds_exact_instance_and_retains_partial_resources(self):
        self.assertEqual(self.launcher.start(self.record,release_id=self.release,api_version=7,workers_version=8),"command")
        kwargs=self.ssm.send_command.call_args.kwargs
        self.assertEqual(kwargs["InstanceIds"],[self.instance["InstanceId"]])
        self.assertEqual(kwargs["DocumentName"],"AWS-RunShellScript")
        self.assertNotIn("terminate",kwargs["Parameters"]["commands"][0])
        self.ec2.terminate_instances.assert_not_called()
        self.assertEqual(launch.assert_main_controller.call_count,2)

    def test_foreign_instance_lock_or_lease_never_submits_bootstrap(self):
        for mutation in ("profile","tag","lock","lease","principal"):
            with self.subTest(mutation=mutation):
                original=copy.deepcopy(self.instance)
                if mutation=="profile":self.instance["IamInstanceProfile"]["Arn"]="foreign"
                elif mutation=="tag":self.instance["Tags"][1]["Value"]="foreign"
                elif mutation=="lock":self.store.client.get_item.return_value={"Item":{}}
                elif mutation=="lease":self.store.read.side_effect=lambda:dict(self.record,revision=2)
                else:self.sts.get_caller_identity.return_value["Account"]="000000000000"
                with self.assertRaises(window.WindowHold):
                    self.launcher.start(self.record,release_id=self.release,api_version=7,workers_version=8)
                self.ssm.send_command.assert_not_called()
                self.instance.clear();self.instance.update(original)
                self.store.client.get_item.return_value={"Item":{"owner":{"S":"candidate:123:1"},"ttl":{"N":"3000"}}}
                self.store.read.side_effect=lambda:copy.deepcopy(self.record)
                self.sts.get_caller_identity.return_value["Account"]=launch.ACCOUNT

    def run_boot(self, *, bad_environment=False, existing_container=False):
        tree=ast.parse(launch.START_PROGRAM)
        tree.body=[node for node in tree.body if not isinstance(node,ast.Try)]
        ns={}
        exec(compile(tree,"<fixed-qa-bootstrap>","exec"),ns)
        writes={};calls=[]
        class FakePath:
            def __init__(self,name):self.name=name
            def is_file(self):return True
            def read_text(self):return "ACADEMY_QA_MODE=isolated-qa\nCANDIDATE_LEASE_REQUIRED=true\n"
            def write_text(self,value):writes[self.name]=value
            def chmod(self,mode):assert mode==0o600
        def run(args):
            calls.append(args)
            if args[:3]==["docker","ps","-aq"]:
                return "existing" if existing_container else ""
            if args[:3]==["aws","ssm","get-parameter"]:
                worker="workers" in args[args.index("--name")+1]
                env={"ACADEMY_RUNTIME_ENV":"development","ACADEMY_DEVELOPMENT_RELEASE_ID":self.release,
                     "DB_NAME":"foreign" if bad_environment else "academy_api_development",
                     "DB_USER":"academy_api_development_app","SOLAPI_MOCK":"true","TOSS_AUTO_BILLING_ENABLED":"false",
                     "VIDEO_BATCH_JOB_QUEUE":"","VIDEO_BATCH_JOB_DEFINITION":"",
                     "TOOLS_SQS_QUEUE_NAME":"academy-v1-development-tools-queue",
                     "MESSAGING_SQS_QUEUE_NAME":"academy-v1-development-messaging-queue",
                     "DJANGO_SETTINGS_MODULE":"apps.api.config.settings.worker" if worker else "apps.api.config.settings.development"}
                for tier in ("LITE","BASIC","PREMIUM"):env["AI_SQS_QUEUE_NAME_"+tier]="academy-v1-development-ai-queue"
                value=json.dumps(env)
                if worker:value=base64.b64encode(value.encode()).decode()
                return json.dumps({"Parameter":{"Version":8 if worker else 7,"Value":value}})
            if args==["redis6-cli","ping"]:return "PONG"
            if args[:3]==["aws","ecr","get-login-password"]:return "synthetic-registry-token"
            return ""
        ns["run"]=run;ns["Path"]=FakePath
        login=Mock(return_value=SimpleNamespace(returncode=0))
        ns["subprocess"]=SimpleNamespace(run=login)
        args=["bootstrap",base64.b64encode(json.dumps(self.plan()).encode()).decode()]
        output=io.StringIO()
        with patch.object(ns["sys"],"argv",args),patch.object(ns["os"],"umask"),redirect_stdout(output):
            if bad_environment or existing_container:
                with self.assertRaises(AssertionError):ns["main"]()
            else:ns["main"]()
        return calls,writes,login,output.getvalue()

    def test_boot_program_enforces_pins_before_start_and_keeps_credentials_out_of_arguments(self):
        calls,writes,login,output=self.run_boot()
        starts=[args for args in calls if args[:2]==["docker","run"]]
        self.assertEqual(len(starts),4)
        self.assertTrue(all("@sha256:" in args[-1] and "/academy-qa-" in args[-1] for args in starts))
        self.assertTrue(all("CANDIDATE_LEASE_REQUIRED=true\n" in env for env in writes.values()))
        self.assertTrue(all("ACADEMY_QA_LEASE_ID="+self.record["lease_id"] in env for env in writes.values()))
        self.assertEqual(login.call_args.kwargs["input"],"synthetic-registry-token")
        self.assertNotIn("synthetic-registry-token",str(login.call_args.args))
        self.assertNotIn("synthetic-registry-token",output)

    def test_foreign_environment_and_partial_boot_never_start_containers(self):
        for kwargs in (dict(bad_environment=True),dict(existing_container=True)):
            calls,_,login,_=self.run_boot(**kwargs)
            self.assertFalse(any(args[:2]==["docker","run"] for args in calls))
            login.assert_not_called()

    def test_inert_launch_retains_baseline_and_recovers_same_client_token(self):
        import yaml
        config=yaml.safe_load((Path(__file__).resolve().parents[1]/"docs/ssot/params.yaml").read_text())
        baseline={"owner":self.record["owner_task"],"lock_owner":"candidate:123:1",
            "instance_id":"i-0fedcba9876543210","ami":config["api"]["amiId"],
            "instance_type":config["api"]["instanceType"],"production_database":"academy"}
        original={"InstanceId":baseline["instance_id"],"VpcId":config["network"]["vpcId"],
                  "SubnetId":"subnet-0123456789abcdef0"}
        self.ec2.describe_instances.return_value={"Reservations":[]}
        self.ec2.describe_security_groups.return_value={"SecurityGroups":[{
            "GroupId":"sg-0123456789abcdef0","VpcId":original["VpcId"],"IpPermissions":[]}]}
        self.ec2.run_instances.return_value={"Instances":[{"InstanceId":self.instance["InstanceId"]}]}
        args=dict(baseline_path="synthetic-snapshot.json",baseline_sha256="d"*64,
            owner_task=self.record["owner_task"],lease_id=self.record["lease_id"],
            source_sha=self.record["source_sha"],images=self.record["images"],
            release_id=self.release,api_version=7,workers_version=8)
        with patch("candidate_slot.load_snapshot",return_value=baseline), \
             patch("candidate_slot.guard",return_value=[original]), \
             patch("candidate_slot.baseline_matches",return_value=True):
            self.assertEqual(self.launcher.launch(**args),self.instance["InstanceId"])
            request=self.ec2.run_instances.call_args.kwargs
            self.assertEqual(request["ClientToken"],self.record["lease_id"])
            tags={v["Key"]:v["Value"] for v in request["TagSpecifications"][0]["Tags"]}
            self.assertEqual(tags["Project"],"academy")
            self.assertEqual(request["MinCount"],1)
            self.assertEqual(request["MaxCount"],1)
            self.assertEqual(request["IamInstanceProfile"],{"Arn":window.PROFILE})
            self.assertNotIn("docker run",request["UserData"])
            existing=dict(self.instance,Tags=request["TagSpecifications"][0]["Tags"])
            self.ec2.describe_instances.return_value={"Reservations":[{"Instances":[existing]}]}
            self.assertEqual(self.launcher.launch(**args),self.instance["InstanceId"])
            self.ec2.run_instances.assert_called_once()
            existing["Tags"][0]["Value"]="foreign"
            with self.assertRaises(window.WindowHold):self.launcher.launch(**args)
        self.ec2.terminate_instances.assert_not_called()
        self.ssm.send_command.assert_not_called()

    def test_open_network_boundary_prevents_instance_creation(self):
        import yaml
        config=yaml.safe_load((Path(__file__).resolve().parents[1]/"docs/ssot/params.yaml").read_text())
        baseline={"owner":self.record["owner_task"],"lock_owner":"candidate:123:1",
            "instance_id":"i-0fedcba9876543210","ami":config["api"]["amiId"],
            "instance_type":config["api"]["instanceType"],"production_database":"academy"}
        original={"InstanceId":baseline["instance_id"],"VpcId":config["network"]["vpcId"],
                  "SubnetId":"subnet-0123456789abcdef0"}
        self.ec2.describe_instances.return_value={"Reservations":[]}
        self.ec2.describe_security_groups.return_value={"SecurityGroups":[{
            "GroupId":"sg-0123456789abcdef0","VpcId":original["VpcId"],"IpPermissions":[{"IpProtocol":"-1"}]}]}
        with patch("candidate_slot.load_snapshot",return_value=baseline), \
             patch("candidate_slot.guard",return_value=[original]), \
             patch("candidate_slot.baseline_matches",return_value=True), self.assertRaises(window.WindowHold):
            self.launcher.launch(baseline_path="synthetic-snapshot.json",baseline_sha256="d"*64,
                owner_task=self.record["owner_task"],lease_id=self.record["lease_id"],
                source_sha=self.record["source_sha"],images=self.record["images"],
                release_id=self.release,api_version=7,workers_version=8)
        self.ec2.run_instances.assert_not_called()
