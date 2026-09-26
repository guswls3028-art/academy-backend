"""Independent nonproduction environment publisher; never reads production envs.

Values stay in memory. Public receipts contain exact parameter versions only.
Publication does not activate a runtime; callers must complete the owned stage gate.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

REGION = "ap-northeast-2"
BASE_KEYS = frozenset({
    "AWS_DEFAULT_REGION", "DB_HOST", "DB_PORT", "DB_SSL_MODE", "SITE_URL",
    "CDN_HLS_BASE_URL", "MATCHUP_HYBRID_VLM_TENANTS",
    "MATCHUP_VLM_FILL_EMPTY_PAGES", "MATCHUP_VLM_PAGE_ROLE_FILTER",
})
MODEL_PINS = {"text": "gemini-2.5-flash-lite", "vision": "gemini-2.5-flash"}
RELEASE = re.compile(r"sha-[0-9a-f]{40}-run-[1-9][0-9]*-[1-9][0-9]*")
STAGES = ("development", "preprod")


class BoundaryError(ValueError):
    """Safe, value-free boundary failure."""


def source_names(stage, provider=False):
    if stage not in STAGES or (provider and stage != "development"):
        raise BoundaryError("Unsupported stage/provider combination")
    result = {
        "api_base": f"/academy/api/{stage}/base-env",
        "db_credentials": f"/academy/api/{stage}/db-credentials",
        "r2_credentials": f"/academy/r2/{stage}/credentials",
    }
    if stage == "development":
        result["workers_base"] = "/academy/workers/development/base-env"
    else:
        result["cdn_credentials"] = "/academy/api/preprod/cdn-credentials"
    if provider:
        result["gemini_key"] = "/academy/providers/development/gemini-api-key"
    return result


def metadata(ssm, stage, provider=False):
    versions = {}
    today = date.today()
    for kind, name in source_names(stage, provider).items():
        response = ssm.describe_parameters(ParameterFilters=[
            {"Key": "Name", "Option": "Equals", "Values": [name]}
        ])
        items = response.get("Parameters", [])
        if response.get("NextToken") or len(items) != 1:
            raise BoundaryError(f"Missing exact source metadata: {kind}")
        item = items[0]
        if item.get("Name") != name or item.get("Type") != "SecureString":
            raise BoundaryError(f"Source must be an exact SecureString: {kind}")
        version = item.get("Version")
        if type(version) is not int or version < 1:
            raise BoundaryError(f"Invalid source version: {kind}")
        tags = {v["Key"]: v["Value"] for v in ssm.list_tags_for_resource(
            ResourceType="Parameter", ResourceId=name
        ).get("TagList", [])}
        if tags.get("Environment") != stage or not tags.get("Owner"):
            raise BoundaryError(f"Missing source ownership: {kind}")
        if kind.endswith("_base") and (
            tags.get("Purpose") != "candidate-base-config" or tags.get("SchemaVersion") != "1"
        ):
            raise BoundaryError(f"Invalid base configuration tags: {kind}")
        if kind == "gemini_key":
            try:
                rotate = date.fromisoformat(tags.get("RotateBy", ""))
            except ValueError:
                raise BoundaryError("Invalid provider rotation date") from None
            if not (today <= rotate <= today + timedelta(days=90)) or not all(
                tags.get(k) for k in ("RotationOwner", "GoogleProjectId")
            ) or tags.get("Purpose") != "matchup-synthetic-qa":
                raise BoundaryError("Provider ownership/rotation contract is incomplete")
        versions[kind] = version
    return versions


def read_sources(ssm, stage, versions, provider=False):
    names = source_names(stage, provider)
    if set(versions) != set(names):
        raise BoundaryError("Source version set differs from exact allowlist")
    values = {}
    for kind, name in names.items():
        version = versions[kind]
        if type(version) is not int or version < 1:
            raise BoundaryError("Invalid pinned source version")
        item = ssm.get_parameter(Name=f"{name}:{version}", WithDecryption=True)["Parameter"]
        if item.get("Name") != name or item.get("Version") != version or item.get("Type") != "SecureString":
            raise BoundaryError(f"Pinned source readback mismatch: {kind}")
        value = item["Value"]
        if kind == "gemini_key":
            if not isinstance(value, str) or not 20 <= len(value) <= 256 or any(c.isspace() for c in value):
                raise BoundaryError("Invalid provider credential format")
            values[kind] = value
        else:
            try:
                values[kind] = json.loads(value)
            except (ValueError, TypeError):
                raise BoundaryError(f"Invalid source JSON: {kind}") from None
            if not isinstance(values[kind], dict):
                raise BoundaryError(f"Source must be a JSON object: {kind}")
    return values


def validate_base(value):
    if not isinstance(value, dict) or set(value) - BASE_KEYS:
        raise BoundaryError("Base configuration contains a non-allowlisted key")
    if any(not isinstance(v, str) or "\n" in v or "\r" in v for v in value.values()):
        raise BoundaryError("Base configuration values must be single-line strings")
    if value.get("AWS_DEFAULT_REGION") != REGION or not re.fullmatch(
        r"[a-zA-Z0-9.-]+", value.get("DB_HOST", "")
    ) or value.get("DB_PORT") != "5432" or value.get("DB_SSL_MODE") != "require":
        raise BoundaryError("Base configuration lacks the reviewed region/DB/TLS contract")
    if value.get("CDN_HLS_BASE_URL", "https://cdn.hakwonplus.com") != "https://cdn.hakwonplus.com":
        raise BoundaryError("Unexpected CDN endpoint")
    return dict(value)


def assemble(stage, release, values, production_database, provider=False):
    if not RELEASE.fullmatch(release) or not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", production_database):
        raise BoundaryError("Invalid release or production denial target")
    if set(values) != set(source_names(stage, provider)):
        raise BoundaryError("Source set does not match requested capability")
    db_name = f"academy_api_{stage}"
    db_user = f"{db_name}_app"
    if production_database == db_name:
        raise BoundaryError("Production database denial target overlaps isolated database")
    credential = values["db_credentials"]
    if credential.get("DB_USER") != db_user or not isinstance(credential.get("DB_PASSWORD"), str) or len(credential["DB_PASSWORD"]) < 32:
        raise BoundaryError("Dedicated database credentials are invalid")
    r2 = values["r2_credentials"]
    required = ("R2_ENDPOINT", "R2_REGION", "R2_ACCESS_KEY", "R2_SECRET_KEY")
    if any(not isinstance(r2.get(k), str) or not r2[k] for k in required):
        raise BoundaryError("Incomplete isolated R2 credential")
    if len(r2["R2_ACCESS_KEY"]) < 16 or len(r2["R2_SECRET_KEY"]) < 32:
        raise BoundaryError("Isolated R2 key material is incomplete")
    if not re.fullmatch(r"https://[a-f0-9]{32}\.r2\.cloudflarestorage\.com/?", r2["R2_ENDPOINT"]) or r2["R2_REGION"] != "auto":
        raise BoundaryError("Invalid isolated R2 endpoint/region")
    common = {k: r2[k] for k in required}
    password = credential["DB_PASSWORD"]
    def derived(label):
        return base64.b64encode(hashlib.sha256(f"academy-{stage}-{label}:{password}".encode()).digest()).decode()
    common.update({
        "ACADEMY_RUNTIME_ENV": stage, f"ACADEMY_{stage.upper()}_RELEASE_ID": release,
        "DB_NAME": db_name, "DB_USER": db_user, "DB_PASSWORD": password,
        "SECRET_KEY": derived("django"), "MESSAGING_TENANT_BINDING_KEY": derived("messaging"),
        "MESSAGING_TENANT_BINDING_FALLBACK_KEYS": "", "AWS_DEFAULT_REGION": REGION,
        "SOLAPI_MOCK": "true", "SOLAPI_API_KEY": "", "SOLAPI_API_SECRET": "",
        "SOLAPI_SENDER": "", "SOLAPI_KAKAO_TEMPLATE_ID": "",
        "SOLAPI_KAKAO_PF_ID": "development-mock-pfid" if stage == "development" else "",
        "MESSAGING_DRY_RUN_TRIGGERS": "" if stage == "development" else "*",
        "TOSS_AUTO_BILLING_ENABLED": "false", "TOSS_PAYMENTS_CLIENT_KEY": "", "TOSS_PAYMENTS_SECRET_KEY": "",
        "BILLING_BANK_TRANSFER_ENABLED": "false", "BILLING_KEY_ENCRYPTION_WRITE_ENABLED": "false",
        "BILLING_KEY_ENCRYPTION_PRIMARY_KEY": "", "BILLING_KEY_ENCRYPTION_FALLBACK_KEYS": "",
        "VAPID_PRIVATE_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "AWS_ACCESS_KEY_ID": "", "AWS_SECRET_ACCESS_KEY": "", "AWS_SESSION_TOKEN": "",
        "GEMINI_API_KEY": "", "SENTRY_ENVIRONMENT": stage,
        "VIDEO_BATCH_JOB_QUEUE": "", "VIDEO_BATCH_JOB_DEFINITION": "",
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379",
        "R2_PUBLIC_BASE_URL": "", "R2_ADMIN_PUBLIC_BASE_URL": "",
        "CDN_HLS_BASE_URL": "https://cdn.hakwonplus.com", "CDN_HLS_SIGNING_KEY_ID": "v1",
        "MATCHUP_VLM_TEXT_ADAPTER": "gemini_flash_lite",
        "MATCHUP_VLM_VISION_ADAPTER": "gemini_flash",
        "MATCHUP_VLM_MAX_OUTPUT_TOKENS": "8192", "MATCHUP_VLM_TIMEOUT_SEC": "90",
    })
    for queue in ("LITE", "BASIC", "PREMIUM"):
        common[f"AI_SQS_QUEUE_NAME_{queue}"] = "academy-v1-development-ai-queue" if stage == "development" else ""
    common["TOOLS_SQS_QUEUE_NAME"] = "academy-v1-development-tools-queue" if stage == "development" else ""
    common["MESSAGING_SQS_QUEUE_NAME"] = "academy-v1-development-messaging-queue" if stage == "development" else ""
    if stage == "development":
        bucket = r2.get("R2_BUCKET", r2.get("R2_BUCKET_NAME", ""))
        if bucket != "academy-development-artifacts":
            raise BoundaryError("R2 bucket must be the dedicated development bucket")
        common["CDN_HLS_SIGNING_SECRET"] = secrets.token_urlsafe(32)
        for kind in ("AI", "VIDEO", "STORAGE", "EXCEL", "ADMIN"):
            common[f"R2_{kind}_BUCKET"] = bucket
    else:
        if r2.get("ACCESS_MODE") != "read-only" or r2.get("R2_VIDEO_BUCKET") != "academy-video":
            raise BoundaryError("Preprod requires the reviewed read-only video credential")
        cdn = values["cdn_credentials"]
        if set(cdn) != {"CDN_HLS_SIGNING_SECRET"} or not isinstance(cdn["CDN_HLS_SIGNING_SECRET"], str) or len(cdn["CDN_HLS_SIGNING_SECRET"]) < 32:
            raise BoundaryError("Preprod requires independently provisioned CDN read credentials")
        common.update(cdn)
        common["ACADEMY_R2_ACCESS_MODE"] = "read-only"
        for kind in ("AI", "VIDEO", "STORAGE", "EXCEL", "ADMIN"):
            common[f"R2_{kind}_BUCKET"] = "academy-video" if kind == "VIDEO" else ""
    api = validate_base(values["api_base"])
    api.update(common)
    api["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.development" if stage == "development" else "apps.api.config.settings.prod"
    workers = None
    if stage == "development":
        workers = validate_base(values["workers_base"])
        if any(workers[k] != values["api_base"][k] for k in ("DB_HOST", "DB_PORT", "DB_SSL_MODE")):
            raise BoundaryError("API and worker database targets differ")
        workers.update(common)
        workers["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
        if provider:
            workers["GEMINI_API_KEY"] = values["gemini_key"]
    for environment in (api, workers):
        if environment is not None and any(
            not isinstance(value, str) or any(c in value for c in ("\n", "\r", "\x00"))
            for value in environment.values()
        ):
            raise BoundaryError("Runtime values must be single-line strings without NUL")
    return api, workers


def publish(ssm, stage, release, production_database, provider=False, receipt_path=None, journal=None):
    versions = metadata(ssm, stage, provider)
    values = read_sources(ssm, stage, versions, provider)
    api, workers = assemble(stage, release, values, production_database, provider)
    receipt = {"stage": stage, "release_id": release, "source_versions": versions,
               "production_database_name": production_database, "model_pins": MODEL_PINS,
               "provider_qa": "required" if provider else "unavailable", "outputs": {}}
    targets = [(f"/academy/api/{stage}/env", api)]
    if workers is not None:
        targets.append(("/academy/workers/development/env", workers))
    receipt["previous_versions"] = {}
    for name, _ in targets:
        found = ssm.describe_parameters(ParameterFilters=[
            {"Key": "Name", "Option": "Equals", "Values": [name]}
        ]).get("Parameters", [])
        if len(found) != 1 or type(found[0].get("Version")) is not int:
            raise BoundaryError("Existing isolated output is required for rollback")
        receipt["previous_versions"][name] = found[0]["Version"]
    if journal and journal.read() is not None:
        raise BoundaryError("Publication journal already exists; restore it before another publication")
    def persist():
        if receipt_path:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        if journal:
            journal.save(receipt)
    persist()
    try:
        for name, value in targets:
            serialized = json.dumps(value, separators=(",", ":"), sort_keys=True)
            if "/workers/" in name:
                serialized = base64.b64encode(serialized.encode()).decode()
            if journal:
                receipt.setdefault("pending", {})[name] = "publish"
                persist()
            result = ssm.put_parameter(Name=name, Type="SecureString", Tier="Advanced", Value=serialized, Overwrite=True)
            version = result.get("Version")
            if type(version) is not int or version < 1:
                raise BoundaryError("Publisher returned no pinned output version")
            receipt["outputs"][name] = version
            receipt.get("pending", {}).pop(name, None)
            persist()
            actual = ssm.get_parameter(Name=f"{name}:{version}", WithDecryption=True)["Parameter"]
            if actual.get("Version") != version or actual.get("Value") != serialized:
                raise BoundaryError("Published environment readback mismatch")
            receipt["outputs"][name] = version
    except Exception:
        if receipt["outputs"]:
            rollback(ssm, receipt, receipt_path=receipt_path, journal=journal)
        raise
    return receipt


def rollback(ssm, receipt, receipt_path=None, journal=None):
    """Retry only acknowledged own versions; ambiguous writes remain HOLD."""
    stage = receipt.get("stage")
    expected = {f"/academy/api/{stage}/env"}
    if stage == "development":
        expected.add("/academy/workers/development/env")
    outputs = receipt.get("outputs", {})
    prior = receipt.get("previous_versions", {})
    restored = receipt.setdefault("restored_versions", {})
    if stage not in STAGES or not prior or not set(prior) <= expected or not set(outputs) <= set(prior) or not set(restored) <= set(outputs):
        raise BoundaryError("Invalid rollback receipt")
    def persist():
        if receipt_path:
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        if journal:
            journal.save(receipt)
    previous = {}
    for name, old_version in prior.items():
        if name in receipt.get("pending", {}):
            continue  # Never guess whether an unacknowledged service write committed.
        wanted = restored.get(name, outputs.get(name, old_version))
        current = ssm.get_parameter(Name=name, WithDecryption=False)["Parameter"]
        if current.get("Version") != wanted:
            raise BoundaryError("Newer environment publication exists; rollback refused")
        if type(old_version) is not int or old_version < 1 or (name in outputs and old_version >= outputs[name]):
            raise BoundaryError("Invalid rollback source version")
        old = ssm.get_parameter(Name=f"{name}:{old_version}", WithDecryption=True)["Parameter"]
        if old.get("Version") != old_version or old.get("Type") != "SecureString":
            raise BoundaryError("Rollback source readback differs")
        if name in restored:
            actual = ssm.get_parameter(Name=f"{name}:{restored[name]}", WithDecryption=True)["Parameter"]
            if actual.get("Value") != old["Value"]:
                raise BoundaryError("Already restored value differs")
        elif name in outputs:
            previous[name] = old["Value"]
    for name, value in previous.items():
        if journal:
            receipt.setdefault("pending", {})[name] = "restore"
            persist()
        version = ssm.put_parameter(Name=name, Type="SecureString", Tier="Advanced", Value=value, Overwrite=True)["Version"]
        restored[name] = version
        receipt.get("pending", {}).pop(name, None)
        persist()
        actual = ssm.get_parameter(Name=f"{name}:{version}", WithDecryption=True)["Parameter"]
        if actual.get("Value") != value or actual.get("Version") != version:
            raise BoundaryError("Rollback publication readback failed")
    if receipt.get("pending"):
        raise BoundaryError("Unacknowledged publication remains; retain journal and HOLD")
    return {"stage": stage, "restored_versions": restored}



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "publish", "rollback"))
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--provider", action="store_true")
    parser.add_argument("--release")
    parser.add_argument("--production-database")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--journal-owner")
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    import boto3
    ssm = boto3.client("ssm", region_name=REGION)
    try:
        journal = None
        if args.journal_owner:
            from candidate_journal import Journal
            journal = Journal(boto3.client("dynamodb", region_name=REGION),
                              args.journal_owner, os.environ["ACADEMY_DEPLOY_LOCK_OWNER"])
        if args.command != "preflight":
            subprocess.run(["python3", str(Path(__file__).with_name("deployment_lock.py")), "assert-owned",
                            "--owner", os.environ["ACADEMY_DEPLOY_LOCK_OWNER"]], check=True)
        if args.command == "rollback":
            saved = journal.read() if journal else None
            if journal and saved is None:
                if not args.baseline:
                    raise BoundaryError("Missing publication journal and baseline")
                baseline = json.loads(args.baseline.read_text())
                prior = {"/academy/api/development/env": baseline["api_version"],
                         "/academy/workers/development/env": baseline["workers_version"]}
                saved = {"stage":"development","outputs":{},"previous_versions":prior}
            elif not journal:
                if not args.receipt:
                    raise BoundaryError("Rollback requires an exact receipt")
                saved = json.loads(args.receipt.read_text())
            result = rollback(ssm, saved, receipt_path=args.receipt, journal=journal)
        elif args.command == "preflight":
            if not args.production_database or not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", args.production_database) or args.production_database == f"academy_api_{args.stage}":
                raise BoundaryError("Preflight requires the reviewed production database denial target")
            result = {"stage": args.stage, "versions": metadata(ssm, args.stage, args.provider)}
        else:
            if not args.release or not args.production_database or not args.receipt:
                raise BoundaryError("Publication requires release, denial target and receipt path")
            result = publish(ssm, args.stage, args.release, args.production_database, args.provider, args.receipt, journal)
            if args.github_output:
                outputs = {
                    "receipt_json": json.dumps(result, separators=(",", ":")),
                    "parameter_version": result["outputs"][f"/academy/api/{args.stage}/env"],
                    "release_id": args.release, "production_database_name": args.production_database,
                    "preprod_database_name": "academy_api_preprod",
                    "preprod_database_user": "academy_api_preprod_app",
                }
                if args.stage == "development":
                    outputs["workers_parameter_version"] = result["outputs"]["/academy/workers/development/env"]
                with args.github_output.open("a") as stream:
                    stream.writelines(f"{k}={v}\n" for k, v in outputs.items())
        if args.receipt and args.command != "rollback":
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(result, indent=2) + "\n")
        print("CANDIDATE_RUNTIME_OK")
    except BoundaryError as exc:
        parser.exit(2, f"CANDIDATE_RUNTIME_BLOCKED: {exc}\n")
    except Exception:
        # SDK exceptions can contain request values; never emit raw provider errors.
        parser.exit(2, "CANDIDATE_RUNTIME_BLOCKED: source/publication service failed; runtime not activated\n")


if __name__ == "__main__":
    main()
