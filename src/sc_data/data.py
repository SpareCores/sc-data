import builtins
import logging
import lzma
import os
import sqlite3
import tempfile
import threading
import time

import requests

from . import constants

logger = logging.getLogger(__name__)

# Cache directory and file names
CACHE_DIR_NAME = "sparecores-data"


def get_parameter(name):
    """Get a parameter either from builtins, envvars or the constants module."""
    return (
        getattr(builtins, f"sc_data_{name}", None)
        or os.environ.get(f"SC_DATA_{name.upper()}")
        or getattr(constants, name.upper(), None)
    )


def get_db_url():
    """Get the database URL (from builtins, env, or constants default)."""
    return get_parameter("db_url") or constants.DB_URL


def get_cache_file_names():
    """Return cache file names for the default database."""
    return ("sc-data-all.db", "sc-data-all.hash")


def get_cache_dir():
    """Get the Unix-conformant cache directory for sparecores-data."""
    # Check XDG_CACHE_HOME first (Linux standard)
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache:
        base_cache = xdg_cache
    elif os.name == "nt":
        # Windows: use LOCALAPPDATA
        base_cache = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    elif os.path.exists(os.path.expanduser("~/Library/Caches")):
        # macOS
        base_cache = os.path.expanduser("~/Library/Caches")
    else:
        # Linux/Unix default
        base_cache = os.path.expanduser("~/.cache")

    return os.path.join(base_cache, CACHE_DIR_NAME)


_TXN_CONTROL = frozenset(("BEGIN TRANSACTION;", "BEGIN;", "COMMIT;"))


def _restore_sql_dump(conn, text_stream, chunk_size=8 * 1024 * 1024):
    """Stream-execute a SQL dump into conn in chunked transactions.

    Strips the dump's own BEGIN/COMMIT, re-wraps each chunk in a transaction,
    and feeds it to executescript() — which runs a tight C-level
    sqlite3_prepare_v2 + sqlite3_step loop with the GIL released.

    The caller should set PRAGMA journal_mode=OFF and synchronous=OFF
    for best performance on temp databases where crash-safety is not needed.
    """
    buf = ""
    for line in text_stream:
        if line.strip() in _TXN_CONTROL:
            continue
        buf += line
        if len(buf) >= chunk_size and ";" in line and sqlite3.complete_statement(buf):
            conn.executescript("BEGIN;\n" + buf + "COMMIT;")
            buf = ""
    if buf.strip():
        conn.executescript("BEGIN;\n" + buf + "COMMIT;")


class Data(threading.Thread):
    daemon = True

    def __init__(self, *args, **kwargs):
        self.updated = threading.Event()
        self.lock = threading.Lock()
        self.error = None  # Store exceptions from daemon thread

        # Get cache directory and file paths
        self.cache_dir = get_cache_dir()
        cache_db_name, cache_hash_name = get_cache_file_names()
        self.cache_db_path = os.path.join(self.cache_dir, cache_db_name)
        self.cache_hash_path = os.path.join(self.cache_dir, cache_hash_name)

        # Check if user explicitly set a custom DB path (via builtins or envvar)
        custom_db_path = getattr(builtins, "sc_data_db_path", None) or os.environ.get(
            "SC_DATA_DB_PATH"
        )
        self._custom_db_path = bool(custom_db_path)

        # Initialize path and hash: use custom path if provided, otherwise None
        # (will be set from cache or download)
        if custom_db_path:
            self.actual_db_path = custom_db_path
            self.actual_db_hash = None  # Hash not tracked for custom paths
        else:
            self.actual_db_path = None
            self.actual_db_hash = None
            # Try to use cached database if available and not stale
            self._init_from_cache()

        super().__init__(*args, **kwargs)

    def _init_from_cache(self):
        """Initialize from cached database if available."""
        if self.actual_db_path is not None:
            return
        try:
            if os.path.exists(self.cache_db_path) and os.path.exists(
                self.cache_hash_path
            ):
                cached_hash = self._read_cached_hash()
                if cached_hash:
                    with self.lock:
                        self.actual_db_path = self.cache_db_path
                        self.actual_db_hash = cached_hash
                    logger.debug("Using cached database (hash: %s)", cached_hash)
                    return
            logger.debug("No cached database found, will download")
        except Exception as e:
            logger.warning("Failed to read cached database, will refresh: %s", e)

    def _ensure_cache_dir(self):
        """Ensure the cache directory exists."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            return True
        except Exception as e:
            logger.warning("Failed to create cache directory %s: %s", self.cache_dir, e)
            return False

    def _read_cached_hash(self):
        """Read the cached hash, return None on failure."""
        try:
            if os.path.exists(self.cache_hash_path):
                with open(self.cache_hash_path, "r") as f:
                    return f.read().strip()
        except Exception as e:
            logger.warning("Failed to read cached hash: %s", e)
        return None

    def _atomic_write_cache(self, response, db_hash):
        """
        Atomically write the database to cache.
        Streams the SQL dump, decompresses with lzma, executes into a temp db,
        then renames on success.
        """
        if not self._ensure_cache_dir():
            return False

        temp_db_path = None
        temp_hash_path = None

        try:
            # Create temp files in the cache directory for atomic rename
            temp_db_fd, temp_db_path = tempfile.mkstemp(
                dir=self.cache_dir, suffix=".db.tmp"
            )
            temp_hash_fd, temp_hash_path = tempfile.mkstemp(
                dir=self.cache_dir, suffix=".hash.tmp"
            )
            os.close(temp_db_fd)
            os.close(temp_hash_fd)

            # Stream download -> lzma decompress -> chunked SQL restore.
            # Temp file gets atomically renamed on success, so we can
            # safely skip journal and fsync for maximum write speed.
            with lzma.open(response.raw, mode="rt", encoding="utf-8") as sql:
                conn = sqlite3.connect(temp_db_path, isolation_level=None)
                try:
                    conn.execute("PRAGMA journal_mode=OFF")
                    conn.execute("PRAGMA synchronous=OFF")
                    _restore_sql_dump(conn, sql)
                finally:
                    conn.close()

            # Write hash to temp file
            with open(temp_hash_path, "w") as temp_hash_file:
                temp_hash_file.write(db_hash)

            # Atomic rename (on Unix, rename is atomic if on same filesystem)
            os.replace(temp_db_path, self.cache_db_path)
            os.replace(temp_hash_path, self.cache_hash_path)

            logger.debug("Atomically wrote database to cache (hash: %s)", db_hash)
            return True

        except Exception as e:
            logger.warning("Failed to write database to cache: %s", e)
            # Clean up temp files on failure
            for temp_path in [temp_db_path, temp_hash_path]:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.unlink(temp_path)
                    except Exception:
                        pass
            return False

    @property
    def path(self):
        with self.lock:
            # If path is None and there's an error, raise it
            if self.actual_db_path is None:
                if self.error is not None:
                    raise self.error
                # If updated event is set but path is still None, something went wrong
                if self.updated.is_set():
                    raise RuntimeError(
                        "Database path is not available: initialization completed but no database path was set"
                    )
            return self.actual_db_path

    @property
    def hash(self):
        with self.lock:
            return self.actual_db_hash

    def update(self):
        """
        Update the database file if necessary.
        Returns True if update succeeded or was not needed, False on failure.
        """
        if self._custom_db_path:
            return True
        t0 = time.time()
        url = get_db_url()
        try:
            logger.debug(
                "update: GET %s (timeout=%s)", url, get_parameter("http_timeout")
            )
            with requests.get(
                url,
                timeout=float(get_parameter("http_timeout")),
                stream=True,
            ) as r:
                elapsed = time.time() - t0
                logger.debug("update: HTTP %d in %.1fs", r.status_code, elapsed)

                # Check for successful response
                if not (200 <= r.status_code < 300):
                    logger.warning(
                        "update: HTTP %d after %.1fs", r.status_code, elapsed
                    )
                    return False

                # Get remote hash; skip refresh when header is missing
                remote_hash = r.headers.get("x-amz-meta-hash")
                if not remote_hash:
                    logger.warning("update: x-amz-meta-hash missing, skipping refresh")
                    return True

                need_download = (
                    self.actual_db_path is None or remote_hash != self.actual_db_hash
                )

                if not need_download:
                    logger.debug(
                        "No need to update database (hash matches: %s)",
                        remote_hash,
                    )
                    return True

                logger.debug(
                    "update: downloading (remote_hash=%s, current_hash=%s)",
                    remote_hash,
                    self.actual_db_hash,
                )

                # Download and write to cache atomically
                if self._atomic_write_cache(r, remote_hash):
                    with self.lock:
                        self.actual_db_path = self.cache_db_path
                        self.actual_db_hash = remote_hash
                    logger.debug(
                        "update: done in %.1fs, path=%s",
                        time.time() - t0,
                        self.cache_db_path,
                    )
                    return True
                else:
                    logger.warning(
                        "update: cache write failed after %.1fs",
                        time.time() - t0,
                    )
                    return False

        except Exception as e:
            logger.warning("update: failed after %.1fs: %s", time.time() - t0, e)
            return False

    def run(self):
        """Start the update thread if no_update is not set."""
        logger.debug("Data thread started; db_url=%s", get_db_url())
        if get_parameter("no_update"):
            logger.warning("Automated database refresh is disabled.")
            self.updated.set()
            return

        # For the first update attempt, retry on failure
        first_attempt = True
        max_retries = 3
        retry_count = 0

        while True:
            success = self.update()
            logger.debug(
                "update() returned %s; path=%r, error=%r",
                success,
                self.actual_db_path,
                self.error,
            )

            # Only signal update completion if it succeeded or if we've exhausted retries
            if success:
                self.updated.set()
                first_attempt = False
                retry_count = 0
            elif first_attempt and retry_count < max_retries:
                retry_count += 1
                retry_delay = 2**retry_count  # Exponential backoff: 2s, 4s, 8s
                logger.info(
                    "Retrying database update (attempt %d/%d) in %d seconds...",
                    retry_count + 1,
                    max_retries + 1,
                    retry_delay,
                )
                time.sleep(retry_delay)
                continue
            elif first_attempt:
                # After max retries, check if we have an existing database
                with self.lock:
                    has_db = self.actual_db_path is not None and os.path.exists(
                        self.actual_db_path
                    )

                if has_db:
                    # We have an existing database, signal completion
                    logger.warning(
                        "Failed to update database after %d attempts, using existing database",
                        max_retries + 1,
                    )
                    self.updated.set()
                else:
                    # No database available, store error and signal completion
                    logger.error(
                        "Failed to download database after %d attempts and no existing database found",
                        max_retries + 1,
                    )
                    with self.lock:
                        self.error = RuntimeError(
                            f"Failed to download database after {max_retries + 1} attempts and no existing database available"
                        )
                    self.updated.set()
                    return  # Exit thread, don't raise
                first_attempt = False

            time.sleep(int(get_parameter("db_refresh_seconds")))
