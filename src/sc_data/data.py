import builtins
import bz2
import collections
import logging
import os
import shutil
import tempfile
import threading
import time

import requests

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
        try:
            os.unlink(tmpfile.name)
        except FileNotFoundError:
            pass
        tmpfile.close()
        tmpfiles.remove(tmpfile)


def handle(f, url=None):
    """Return the original or wrapped file handle depending on the file name."""
    if url.endswith("bz2"):
        return bz2.BZ2File(f, mode="rb")
    else:
        return f


class Data(threading.Thread):
    daemon = True

    def __init__(self, *args, **kwargs):
        self.tmpfiles = collections.deque()
        self.updated = threading.Event()
        self.lock = threading.Lock()
        self.actual_db_path = get_parameter("db_path")
        # initialize with embedded DB hash
        self.actual_db_hash = constants.DB_HASH
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
            and (db_hash := r.headers.get("x-amz-meta-hash", time.time()))
            != self.actual_db_hash
        ):
            # delete=False due to Windows support
            # https://stackoverflow.com/questions/15588314/cant-access-temporary-files-created-with-tempfile/15590253#15590253
            tmpfile = tempfile.NamedTemporaryFile(delete=False)
            # use the original, or a decompressor-wrapped file handle
            fh = handle(r.raw, url=get_parameter("db_url"))
            shutil.copyfileobj(fh, tmpfile)
            tmpfile.flush()
            with self.lock:
                self.actual_db_path = tmpfile.name
                self.actual_db_hash = db_hash
            close_tmpfiles(self.tmpfiles)
            self.tmpfiles.append(tmpfile)
            logging.debug("Updated database to hash %s", db_hash)
        else:
            logging.debug("No need to udpate database")

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
