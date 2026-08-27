from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
REFRESH_API_ENV = REPO_ROOT / "scripts" / "v1" / "inline" / "refresh-api-env.sh"


@pytest.mark.skipif(os.name == "nt", reason="executes the Linux host rollback script")
def test_failed_refresh_restores_previous_env_and_container(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is unavailable")

    fake_bin = tmp_path / "bin"
    docker_state = tmp_path / "docker-state"
    fake_bin.mkdir()
    docker_state.mkdir()
    (docker_state / "academy-api").write_text("running", encoding="utf-8")

    fake_commands = {
        "aws": """
            #!/bin/bash
            printf '%s' "$FAKE_ENV_JSON"
        """,
        "curl": """
            #!/bin/bash
            printf '503'
        """,
        "sleep": """
            #!/bin/bash
            exit 0
        """,
        "docker": """
            #!/bin/bash
            set -euo pipefail
            command="$1"
            shift
            case "$command" in
              inspect)
                name="$1"
                test -f "$FAKE_DOCKER_STATE/$name"
                if [ "${2:-}" = "--format" ]; then
                  printf 'example.invalid/academy-api@sha256:%064d\n' 0
                fi
                ;;
              rm)
                [ "${1:-}" = "-f" ] && shift
                rm -f "$FAKE_DOCKER_STATE/$1"
                ;;
              stop)
                test -f "$FAKE_DOCKER_STATE/$1"
                ;;
              rename)
                mv "$FAKE_DOCKER_STATE/$1" "$FAKE_DOCKER_STATE/$2"
                ;;
              start)
                test -f "$FAKE_DOCKER_STATE/$1"
                ;;
              run)
                name=""
                while [ "$#" -gt 0 ]; do
                  if [ "$1" = "--name" ]; then
                    name="$2"
                    break
                  fi
                  shift
                done
                test -n "$name"
                printf 'running' > "$FAKE_DOCKER_STATE/$name"
                printf 'fake-container-id\n'
                ;;
              logs)
                exit 0
                ;;
              *)
                printf 'unexpected docker command: %s\n' "$command" >&2
                exit 90
                ;;
            esac
        """,
    }
    for name, source in fake_commands.items():
        path = fake_bin / name
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        path.chmod(0o755)

    env_file = tmp_path / "api.env"
    previous = b"DJANGO_SETTINGS_MODULE=apps.api.config.settings.prod\nOLD_VALUE=keep-me\n"
    env_file.write_bytes(previous)
    env = os.environ.copy()
    env.update(
        {
            "ACADEMY_API_ENV_FILE": str(env_file),
            "FAKE_DOCKER_STATE": str(docker_state),
            "FAKE_ENV_JSON": (
                '{"DJANGO_SETTINGS_MODULE":"apps.api.config.settings.prod",'
                '"NEW_VALUE":"must-be-rolled-back"}'
            ),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )

    completed = subprocess.run(
        [bash, str(REFRESH_API_ENV)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 1, completed.stderr
    assert env_file.read_bytes() == previous
    assert (docker_state / "academy-api").is_file()
    assert not (docker_state / "academy-api-rollback").exists()
    assert not Path(f"{env_file}.previous").exists()
    assert "API env refresh health failed" in completed.stderr
    assert "API_ENV_REFRESH_ROLLBACK_PASS previous container restored" in completed.stderr
