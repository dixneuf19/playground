import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "server.py", str(port)],
        cwd=APP_DIR,
        env={**os.environ, "ADMIN_PASSWORD": "test-password"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield url
    proc.terminate()
    proc.wait(timeout=5)


def test_serves_index(base_url):
    with urllib.request.urlopen(base_url + "/") as resp:
        assert resp.status == 200
        assert "Sam Marsalis" in resp.read().decode()


def test_serves_stylesheet(base_url):
    with urllib.request.urlopen(base_url + "/css/style.css") as resp:
        assert resp.status == 200


def test_admin_redirects_to_login(base_url):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    with pytest.raises(urllib.error.HTTPError) as exc:
        opener.open(base_url + "/admin/")
    assert exc.value.code == 302
    assert exc.value.headers["Location"] == "/admin/login"


def test_admin_login_with_password(base_url):
    data = b"password=test-password"
    with urllib.request.urlopen(base_url + "/admin/login", data=data) as resp:
        assert resp.status == 200
