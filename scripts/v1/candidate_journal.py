"""Nonsecret recovery journal, atomically fenced by the shared deployment lock."""
from __future__ import annotations
import json
import os
import re
import time

KEY_PREFIX = "__candidate_publication__:"
LOCK_KEY = "__deployment_control_v2__"


class Journal:
    def __init__(self, client, identity, owner):
        if not re.fullmatch(r"candidate:[1-9][0-9]*:[1-9][0-9]*", identity):
            raise ValueError("Invalid journal identity")
        if not re.fullmatch(r"candidate:[1-9][0-9]*:[1-9][0-9]*", owner) or identity.split(":")[1] != owner.split(":")[1]:
            raise ValueError("Journal belongs to another workflow run")
        self.client, self.owner = client, owner
        self.table = os.environ.get("ACADEMY_DEPLOY_LOCK_TABLE", "academy-v1-video-job-lock")
        self.key = KEY_PREFIX + identity
        self.revision = None

    def read(self):
        item = self.client.get_item(TableName=self.table, Key={"videoId":{"S":self.key}},
                                    ConsistentRead=True).get("Item")
        if not item:
            self.revision = None
            return None
        self.revision = int(item["revision"]["N"])
        return json.loads(item["receipt"]["S"])

    def save(self, receipt):
        # This is a metadata journal, never a credential store.
        allowed = {"stage","release_id","source_versions","production_database_name","model_pins",
                   "provider_qa","outputs","previous_versions","restored_versions","pending"}
        if set(receipt) - allowed:
            raise ValueError("Unexpected journal field")
        for field in ("outputs","previous_versions","restored_versions"):
            for name, version in receipt.get(field,{}).items():
                if name not in ("/academy/api/development/env","/academy/workers/development/env") or type(version) is not int or version < 1:
                    raise ValueError("Invalid journal coordinate")
        revision = 1 if self.revision is None else self.revision + 1
        put = {"TableName":self.table, "Item":{
            "videoId":{"S":self.key}, "revision":{"N":str(revision)},
            "receipt":{"S":json.dumps(receipt,sort_keys=True,separators=(",",":"))}},
            "ConditionExpression":"attribute_not_exists(videoId)" if self.revision is None else "revision = :revision"}
        if self.revision is not None:
            put["ExpressionAttributeValues"] = {":revision":{"N":str(self.revision)}}
        self.client.transact_write_items(TransactItems=[
            {"ConditionCheck":{"TableName":self.table,"Key":{"videoId":{"S":LOCK_KEY}},
                "ConditionExpression":"#owner = :owner AND #ttl > :now",
                "ExpressionAttributeNames":{"#owner":"owner","#ttl":"ttl"},
                "ExpressionAttributeValues":{":owner":{"S":self.owner},":now":{"N":str(int(time.time()))}}}},
            {"Put":put}])
        self.revision = revision
