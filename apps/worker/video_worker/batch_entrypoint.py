#!/usr/bin/env python3
"""
Batch entrypoint: fetch /academy/workers/env from SSM, set os.environ, then exec batch_main.
Job role must have ssm:GetParameter for academy/*.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

REGION = os.environ.get("AWS_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "ap-northeast-2"
SSM_NAME = os.environ.get("BATCH_SSM_ENV", "/academy/workers/env")


def main() -> int:
    try:
        import boto3

        client = boto3.client("ssm", region_name=REGION)
        r = client.get_parameter(Name=SSM_NAME, WithDecryption=True)
        content = r["Parameter"]["Value"]
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if val.startswith("'") and val.endswith("'") or val.startswith('"') and val.endswith('"'):
                    val = val[1:-1].replace("\\'", "'").replace('\\"', '"')
                os.environ[key] = val
    except Exception as e:
        print(f"batch_entrypoint: SSM fetch failed: {e}", file=sys.stderr)
        # Continue - maybe env is injected another way

    # CMD from Batch is passed as args: python -m batch_main <job_id>
    argv = sys.argv[1:] if len(sys.argv) > 1 else ["python", "-m", "apps.worker.video_worker.batch_main"]
    if argv[0] == "python" or argv[0].endswith("python"):
        os.execvp(argv[0], argv)
    else:
        os.execv(argv[0], argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
