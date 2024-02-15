import builtins
import collections
import hashlib
import logging
import os
import requests
import shutil
import tempfile
import threading
import time
from . import constants


def get_parameter(name):
    """Get a parameter either from builtins, envvars or the constants module."""
    return (
        getattr(builtins, f"sc_data_{name}", None)
        or os.environ.get(f"SC_DATA_{name.upper()}")
        or getattr(constants, name.upper(), None)
    )


def close_tmpfiles(tmpfiles):
    """Close/remove temporary files."""
    for tmpfile in tmpfiles.copy():
        tmpfile.close()
        tmpfiles.remove(tmpfile)


def get_hash(path, hashfunc=hashlib.sha256):
    """Create a hex digest for a given path."""
    h = hashfunc()
    with open(path, "rb") as f:
        while chunk := f.read(16 * 1024):
            h.update(chunk)
    return h.hexdigest()


class Data(threading.Thread):
    daemon = True

    def __init__(self, *args, **kwargs):
        self.etag = None
        self.tmpfiles = collections.deque()
        self.updated = threading.Event()
        self.lock = threading.Lock()
        self.actual_db_path = get_parameter("db_path")
        self.actual_db_hash = get_hash(self.actual_db_path)
        super().__init__(*args, **kwargs)

    @property
    def path(self):
        with self.lock:
            return self.actual_db_path

    @property
    def hash(self):
        with self.lock:
            return self.actual_db_hash

    def update(self):
        """Update the database file if necessary."""

        # initiate a streaming GET, to receive the headers, but halt the content
        r = requests.get(
            get_parameter("db_url"),
            timeout=float(get_parameter("http_timeout")),
            stream=True,
        )
        # fetch the file if necessary (200 status and etag is missing or different than previous)
        if (
            200 <= r.status_code < 300
            and (etag := r.headers.get("etag", time.time())) != self.etag
        ):
            tmpfile = tempfile.NamedTemporaryFile()
            shutil.copyfileobj(r.raw, tmpfile)
            tmpfile.flush()
            hexdigest = get_hash(tmpfile.name)
            with self.lock:
                self.actual_db_path = tmpfile.name
                self.actual_db_hash = hexdigest
            self.etag = etag
            close_tmpfiles(self.tmpfiles)
            self.tmpfiles.append(tmpfile)

    def run(self):
        """Start the update thread if no_update is not set."""
        if get_parameter("no_update"):
            self.updated.set()
            return
        while True:
            try:
                self.update()
            except Exception:
                logging.exception("Failed to update the database")
            self.updated.set()
            time.sleep(int(get_parameter("db_refresh_seconds")))
