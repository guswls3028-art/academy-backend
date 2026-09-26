"""Root metadata tests only. No creation, credential, DB or storage calls."""
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest

from scripts.v1.candidate_qa_manifest import SCHEMA, encode_root, load_root, owned_prefixes
from scripts.v1.candidate_qa_window import WindowHold, binding

BUCKET = "academy-development-artifacts"


def root():
    return {
        "schema": SCHEMA, "lease_id": "a"*32,
        "owner_task": "01a0d04a-d64f-7473-9f58-61a8e983dcc0",
        "source_sha": "b"*40,
        "images": {kind:"sha256:"+"c"*64 for kind in ("api","ai","tools","messaging")},
        "scope": [509,511], "creator":"frontend-development-qa/v1",
        "tenants":[{"id":101,"code":"qa-owned-fixture","user_ids":[201,202],
                    "creation_seal_sha256":"d"*64}],
        "initial_fixtures":[
            {"tenant_id":101,"model":"core.Tenant","ids":[101],"state_sha256":"e"*64},
            {"tenant_id":101,"model":"core.User","ids":[201,202],"state_sha256":"f"*64},
        ],
        "domains":["auth","ppt","matchup","omr"],
        "storage":[{"tenant_id":101,"bucket":BUCKET,"prefixes":sorted(owned_prefixes(101))}],
    }


class RootManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/"root.json"
        self.value=root()
        self.record={key:copy.deepcopy(self.value[key]) for key in
                     ("lease_id","owner_task","source_sha","images","scope")}
        self.record.update(tenant_ids=[101],lock_owner="candidate:123:1",
            endpoint="ssm://i-0123456789abcdef0:8000",
            profile="arn:aws:iam::809466760795:instance-profile/academy-api-qa",
            baseline_sha256="1"*64,message_key_version=1)
        self.save()

    def save(self):
        raw=encode_root(self.value,expected_bucket=BUCKET)
        self.path.write_bytes(raw)
        self.record["resource_manifest_sha256"]=hashlib.sha256(raw).hexdigest()

    def load(self):
        return load_root(self.path,self.record,expected_bucket=BUCKET)

    def test_root_then_committed_hash_then_binding_without_recursive_hash(self):
        manifest=self.load()
        self.assertEqual(manifest.sha256,self.record["resource_manifest_sha256"])
        self.assertEqual(len(binding(self.record)),64)
        self.assertNotIn("binding_sha256",manifest.data)
        self.assertEqual(len(manifest.initial_fixtures_sha256),64)

    def test_changed_bytes_or_committed_digest_are_rejected(self):
        self.path.write_bytes(self.path.read_bytes()+b" ")
        with self.assertRaises(WindowHold):self.load()
        self.save()
        self.record["resource_manifest_sha256"]="0"*64
        with self.assertRaises(WindowHold):self.load()

    def test_rehashed_foreign_root_still_cannot_replace_scope(self):
        for key,other in (("lease_id","2"*32),("owner_task","00000000-0000-0000-0000-000000000001"),
                          ("source_sha","3"*40)):
            with self.subTest(key=key):
                self.value=root();self.value[key]=other;self.save()
                with self.assertRaises(WindowHold):self.load()
        self.value=root();self.save()
        for key,other in (("tenant_ids",[102]),("scope",[509])):
            saved=copy.deepcopy(self.record);self.record[key]=other
            with self.assertRaises(WindowHold):self.load()
            self.record=saved

    def test_root_forbids_derived_binding_or_secret_fields(self):
        for key in ("binding_sha256","password","api_key","capability"):
            value=root();value[key]="never-allowed"
            with self.assertRaises(WindowHold):encode_root(value,expected_bucket=BUCKET)

    def test_storage_cannot_escape_exact_owned_tenant_or_bucket(self):
        for prefixes in (["tenants/"],["tenants/102/"],["tenants/101/../102/"],
                         ["tenants/101/"]):
            value=root();value["storage"][0]["prefixes"]=prefixes
            with self.assertRaises(WindowHold):encode_root(value,expected_bucket=BUCKET)
        value=root();value["storage"][0]["bucket"]="academy-production"
        with self.assertRaises(WindowHold):encode_root(value,expected_bucket=BUCKET)

    def test_tenant_user_and_domain_inventory_cannot_be_partial_or_ambiguous(self):
        cases=[]
        value=root();value["tenants"][0]["code"]="customer";cases.append(value)
        value=root();value["tenants"][0]["user_ids"]=[201,201];cases.append(value)
        value=root();value["tenants"].append(copy.deepcopy(value["tenants"][0]));cases.append(value)
        value=root();value["domains"].remove("omr");cases.append(value)
        value=root();value["initial_fixtures"]=[];cases.append(value)
        value=root();value["tenants"][0]["creation_seal_sha256"]="unknown";cases.append(value)
        for value in cases:
            with self.assertRaises(WindowHold):encode_root(value,expected_bucket=BUCKET)

    def test_duplicate_json_key_cannot_change_interpretation(self):
        raw=self.path.read_text()
        raw=raw.replace('"schema":', '"lease_id":"duplicate","schema":',1)
        self.path.write_text(raw)
        self.record["resource_manifest_sha256"]=hashlib.sha256(raw.encode()).hexdigest()
        with self.assertRaises(WindowHold):self.load()

    def test_loaded_root_cannot_be_mutated_through_returned_data(self):
        manifest=self.load()
        data=manifest.data;data["tenants"][0]["user_ids"].append(999)
        self.assertEqual(manifest.data["tenants"][0]["user_ids"],[201,202])

    def test_fixture_hash_covers_approved_state_not_list_order(self):
        before=self.load().initial_fixtures_sha256
        self.value["initial_fixtures"].reverse()
        self.value["initial_fixtures"][0]["ids"].reverse()
        self.save()
        self.assertEqual(self.load().initial_fixtures_sha256,before)
        self.value["initial_fixtures"][0]["state_sha256"]="0"*64
        self.save()
        self.assertNotEqual(self.load().initial_fixtures_sha256,before)

    def test_null_identity_and_boolean_tenant_reference_are_rejected(self):
        for key in ("lease_id","owner_task","source_sha"):
            value=root();value[key]=None
            with self.assertRaises(WindowHold):encode_root(value,expected_bucket=BUCKET)
        value=root();value["initial_fixtures"][0]["tenant_id"]=True
        with self.assertRaises(WindowHold):encode_root(value,expected_bucket=BUCKET)

    def test_oversized_root_and_nonregular_path_fail(self):
        self.path.write_bytes(b"x"*(256*1024+1))
        with self.assertRaises(WindowHold):self.load()
        with self.assertRaises(WindowHold):
            load_root(self.path.parent,self.record,expected_bucket=BUCKET)
