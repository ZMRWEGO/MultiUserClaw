"""Debug test: why does /api/status return 503 for httpx but 200 for curl?"""

import subprocess
import httpx
import urllib.request

BASE = "http://127.0.0.1:18080"
URL = f"{BASE}/api/status"


def test_curl():
    result = subprocess.run(
        ["curl", "-s", "-w", "\\n%{http_code}", URL],
        capture_output=True,
        text=True,
    )
    print(f"curl stdout: {result.stdout!r}")
    print(f"curl stderr: {result.stderr!r}")
    lines = result.stdout.strip().split("\n")
    status = int(lines[-1])
    assert status == 200, f"curl expected 200, got {status}"


def test_httpx_get():
    resp = httpx.get(URL, timeout=10)
    print(f"httpx status={resp.status_code} headers={dict(resp.headers)}")
    print(f"httpx body={resp.text!r}")
    assert resp.status_code == 200, f"httpx expected 200, got {resp.status_code}"


def test_httpx_request_no_body():
    resp = httpx.request("GET", URL, timeout=10)
    print(f"httpx.request status={resp.status_code} headers={dict(resp.headers)}")
    assert resp.status_code == 200


def test_httpx_request_empty_body():
    resp = httpx.request("GET", URL, content=b"", timeout=10)
    print(f"httpx.request(empty body) status={resp.status_code}")
    assert resp.status_code == 200


def test_urllib():
    req = urllib.request.Request(URL, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"urllib status={resp.status}")
            assert resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"urllib error: {e.code} {e.reason}")
        raise


def test_socket_raw():
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(("127.0.0.1", 18080))
    request = b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:18080\r\n\r\n"
    sock.sendall(request)
    response = sock.recv(4096)
    sock.close()
    status_line = response.split(b"\r\n")[0].decode()
    print(f"raw socket: {status_line}")
    assert b"200 OK" in response, f"raw socket expected 200, got {status_line}"
