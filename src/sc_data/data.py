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
    """Get the database URL, constructing it from DB_TYPE if DB_URL is not explicitly set."""
    # Check if user explicitly set a custom URL
    explicit_url = (
        getattr(builtins, "sc_data_db_url", None) or os.environ.get("SC_DATA_DB_URL")
    )
    if explicit_url:
        return explicit_url

    # Otherwise, construct URL from database type
    db_type = get_parameter("db_type") or "full"
    if db_type not in ("full", "priceless"):
        logger.warning(
            "Invalid db_type '%s', expected 'full' or 'priceless'. Using 'full'.",
            db_type,
        )
        db_type = "full"

    filename = "sc-data-all.sql.xz" if db_type == "full" else "sc-data-priceless.sql.xz"
    base_url = get_parameter("db_base_url") or constants.DB_BASE_URL
    return f"{base_url}/{filename}"


def get_cache_file_names():
    """Get cache file names based on database type."""
    db_type = get_parameter("db_type") or "full"
    if db_type not in ("full", "priceless"):
        db_type = "full"
    suffix = db_type if db_type == "priceless" else "all"
    return (
        f"sc-data-{suffix}.db",
        f"sc-data-{suffix}.hash",
    )


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

        # Get cache directory and file paths
        self.cache_dir = get_cache_dir()
        cache_db_name, cache_hash_name = get_cache_file_names()
        self.cache_db_path = os.path.join(self.cache_dir, cache_db_name)
        self.cache_hash_path = os.path.join(self.cache_dir, cache_hash_name)

        # Check if user explicitly set a custom DB path (via builtins or envvar)
        custom_db_path = getattr(builtins, "sc_data_db_path", None) or os.environ.get(
            "SC_DATA_DB_PATH"
        )

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
        """Initialize from cached database if available and not stale."""
        try:
            if os.path.exists(self.cache_db_path) and os.path.exists(
                self.cache_hash_path
            ):
                # Check if cache is stale based on file modification time
                cache_mtime = os.path.getmtime(self.cache_db_path)
                cache_age = time.time() - cache_mtime
                cache_ttl = float(
                    get_parameter("db_cache_ttl") or constants.DB_CACHE_TTL
                )

                if cache_age < cache_ttl:
                    # Cache is still valid, use it
                    with open(self.cache_hash_path, "r") as f:
                        cached_hash = f.read().strip()

                    if cached_hash:
                        with self.lock:
                            self.actual_db_path = self.cache_db_path
                            self.actual_db_hash = cached_hash
                        logger.debug(
                            "Using cached database (age: %.0fs, hash: %s)",
                            cache_age,
                            cached_hash,
                        )
                        return
                else:
                    logger.debug(
                        "Cached database is stale (age: %.0fs > TTL: %.0fs)",
                        cache_age,
                        cache_ttl,
                    )
            else:
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

    def _is_cache_stale(self):
        """Check if the cache is stale based on TTL."""
        try:
            if not os.path.exists(self.cache_db_path):
                return True
            cache_mtime = os.path.getmtime(self.cache_db_path)
            cache_age = time.time() - cache_mtime
            cache_ttl = float(get_parameter("db_cache_ttl") or constants.DB_CACHE_TTL)
            return cache_age >= cache_ttl
        except Exception:
            return True

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
        try:
            # Initiate a streaming GET to receive headers first
            r = requests.get(
                get_db_url(),
                timeout=float(get_parameter("http_timeout")),
                stream=True,
            )

            # Check for successful response
            if not (200 <= r.status_code < 300):
                logger.warning("Failed to fetch database: HTTP %d", r.status_code)
                r.close()
                return False

            # Get remote hash
            remote_hash = r.headers.get("x-amz-meta-hash")
            if not remote_hash:
                # Use timestamp as fallback hash if header is missing
                remote_hash = str(time.time())

            # Check if we need to download:
            # 1. Hash changed
            # 2. Cache is stale (TTL exceeded)
            cached_hash = self._read_cached_hash()
            cache_stale = self._is_cache_stale()

            # Need to download if:
            # 1. No database path set yet (first run or cache missing)
            # 2. Hash changed
            # 3. Cache is stale (TTL exceeded)
            need_download = (
                self.actual_db_path is None
                or remote_hash != self.actual_db_hash
                or remote_hash != cached_hash
                or cache_stale
            )

            if not need_download:
                r.close()
                logger.debug("No need to update database (hash matches, cache fresh)")
                return True

            logger.debug(
                "Downloading new SQLite database (remote_hash=%s, cached_hash=%s, stale=%s)",
                remote_hash,
                cached_hash,
                cache_stale,
            )

            # Download and write to cache atomically
            if self._atomic_write_cache(r, remote_hash):
                with self.lock:
                    self.actual_db_path = self.cache_db_path
                    self.actual_db_hash = remote_hash
                logger.debug("Updated database to hash %s", remote_hash)
                return True
            else:
                # Cache write failed
                logger.warning("Cache write failed")
                return False

        except Exception as e:
            logger.warning("Failed to update database: %s", e)
            return False

    def run(self):
        """Start the update thread if no_update is not set."""
        if get_parameter("no_update"):
            logger.warn("Automated database refresh is disabled.")
            self.updated.set()
            return

        # For the first update attempt, retry on failure
        first_attempt = True
        max_retries = 3
        retry_count = 0

        while True:
            success = self.update()

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
                    # No database available, fail
                    logger.error(
                        "Failed to download database after %d attempts and no existing database found",
                        max_retries + 1,
                    )
                    raise RuntimeError(
                        f"Failed to download database after {max_retries + 1} attempts and no existing database available"
                    )
                first_attempt = False

            time.sleep(int(get_parameter("db_refresh_seconds")))
