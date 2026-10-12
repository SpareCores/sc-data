"""Parallel download tests against a local, deliberately misbehaving server.

The real CDN can't be made to stall, drop connections or republish the file
on demand, so these tests serve a payload from 127.0.0.1 instead.
"""

import http.server
import math
import os
import re
import sqlite3
import threading
import time

import pytest

from sc_data import data

PAYLOAD = os.urandom(1_000_000)
ETAG = '"v1"'


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        server = self.server
        with server.lock:
            server.requests += 1
            etag = ETAG if server.requests <= server.republish_after else '"v2"'
        if server.status != 206:
            return self._empty(server.status)
        if self.headers.get("If-Match") != etag:
            return self._empty(412)
        start, end = map(
            int, re.match(r"bytes=(\d+)-(\d+)", self.headers["Range"]).groups()
        )
        body = PAYLOAD[start : end + 1]
        with server.lock:
            first_try = start not in server.seen
            server.seen.add(start)
        self.send_response(206)
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            if server.mode == "flaky" and first_try:
                # drop the connection halfway, but serve a retry from the
                # same offset in full, so the download always finishes
                self.wfile.write(body[: len(body) // 2])
                self.close_connection = True
            elif server.mode == "trickle":
                step = server.trickle_bytes
                for i in range(0, len(body), step):
                    self.wfile.write(body[i : i + step])
                    self.wfile.flush()
                    time.sleep(0.1)
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _empty(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(data, "DOWNLOAD_CHUNK_SIZE", 64 * 1024)
    monkeypatch.setattr(data, "DOWNLOAD_ATTEMPT_SECONDS", 1)
    monkeypatch.setenv("SC_DATA_DOWNLOAD_THREADS", "4")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.lock = threading.Lock()
    srv.requests = 0
    srv.seen = set()
    srv.mode = "ok"
    srv.trickle_bytes = 100  # per 0.1s
    srv.status = 206
    srv.republish_after = float("inf")
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}/sc-data-all.sql.xz"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def download(server, tmp_path, timeout=60):
    path = tmp_path / "dump.xz"
    data._download(server.url, ETAG, len(PAYLOAD), path, time.monotonic() + timeout)
    return path.read_bytes()


def test_download(server, tmp_path):
    assert download(server, tmp_path) == PAYLOAD


def test_dropped_connections_resume(server, tmp_path):
    server.mode = "flaky"
    assert download(server, tmp_path) == PAYLOAD
    assert server.requests > math.ceil(len(PAYLOAD) / data.DOWNLOAD_CHUNK_SIZE)


def test_slow_connections_are_retried(server, tmp_path):
    # 40 KB/s per connection: every 1s attempt is cut by the watchdog after a
    # few 16 KiB blocks, and the next attempt resumes from there
    server.mode = "trickle"
    server.trickle_bytes = 4_000
    size = 200_000
    path = tmp_path / "dump.xz"
    data._download(server.url, ETAG, size, path, time.monotonic() + 60)
    assert path.read_bytes() == PAYLOAD[:size]
    assert server.requests > math.ceil(size / data.DOWNLOAD_CHUNK_SIZE)


def test_deadline(server, tmp_path):
    server.mode = "trickle"
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        download(server, tmp_path, timeout=2)
    assert time.monotonic() - t0 < 3


def test_republished_during_download(server, tmp_path):
    server.republish_after = 3
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="changed during the download"):
        download(server, tmp_path)
    assert time.monotonic() - t0 < 5


def test_client_error_fails_fast(server, tmp_path):
    server.status = 404
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="HTTP 404"):
        download(server, tmp_path)
    assert time.monotonic() - t0 < 5


def test_import_deadline():
    conn = sqlite3.connect(":memory:")
    sql = ["CREATE TABLE t (x);\n"] + ["INSERT INTO t VALUES (1);\n"] * 100
    with pytest.raises(TimeoutError):
        data._restore_sql_dump(conn, iter(sql), time.monotonic() - 1, chunk_size=10)
