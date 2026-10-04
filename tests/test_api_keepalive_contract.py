"""Keep API target connections alive beyond the ALB's 60-second idle timeout."""

import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

import pytest


def api_command():
    source = (Path(__file__).resolve().parents[1] / "docker/api/Dockerfile").read_text(encoding="utf-8")
    return json.loads(source.split("\nCMD ", 1)[1].replace("\\\n", ""))[2]


def test_api_keepalive_exceeds_load_balancer_idle_timeout():
    match = re.search(r"--keep-alive\s+(\d+)", api_command())
    assert match and int(match.group(1)) > 60


@pytest.mark.skipif(sys.platform == "win32", reason="Gunicorn is a POSIX server")
@pytest.mark.parametrize("use_old_timeout", [False, True], ids=["configured", "old-default"])
def test_actual_api_command_reuses_connection_after_old_two_second_expiry(tmp_path, use_old_timeout):
    (tmp_path / "keepalive_probe.py").write_text(
        "def application(environ, start_response):\n"
        "    start_response('200 OK', [('Content-Length', '2')])\n"
        "    return [b'OK']\n"
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        command = api_command().replace("--bind 0.0.0.0:8000", f"--bind fd://{listener.fileno()}")
        command = command.replace("apps.api.config.wsgi:application", "keepalive_probe:application")
        if use_old_timeout:
            command = re.sub(r"--keep-alive\s+\d+", "--keep-alive 2", command)
        env = dict(os.environ, GUNICORN_WORKERS="1", GUNICORN_WORKER_CONNECTIONS="10", PYTHONPATH=str(tmp_path))
        env.pop("GUNICORN_CMD_ARGS", None)
        with (tmp_path / "gunicorn.log").open("w") as log:
            process = subprocess.Popen(
                ["sh", "-c", command], cwd=tmp_path, env=env,
                pass_fds=(listener.fileno(),), start_new_session=True,
                stdout=log, stderr=log,
            )
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                assert response.status == 200 and response.read() == b"OK"
                original_socket = connection.sock
                assert original_socket is not None
                time.sleep(3)
                if use_old_timeout:
                    with pytest.raises((http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError)):
                        connection.request("GET", "/healthz")
                        connection.getresponse()
                    return
                connection.request("GET", "/healthz")
                response = connection.getresponse()
                assert response.status == 200 and response.read() == b"OK"
                assert connection.sock is original_socket, "The probe must not reconnect to conceal idle expiry"
            except Exception:
                log.flush()
                print((tmp_path / "gunicorn.log").read_text(encoding="utf-8")[-2000:])
                raise
            finally:
                connection.close()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
