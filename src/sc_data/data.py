import builtins
import bz2
import logging
import os
import shutil
import tempfile
import threading
import time

import requests

from . import constants

logger = logging.getLogger(__name__)

# Cache directory and file names
CACHE_DIR_NAME = "sparecores-data"
CACHE_DB_NAME = "sc-data.db"
CACHE_HASH_NAME = "sc-data.hash"


def get_parameter(name):
    """Get a parameter either from builtins, envvars or the constants module."""
    return (
        getattr(builtins, f"sc_data_{name}", None)
        or os.environ.get(f"SC_DATA_{name.upper()}")
        or getattr(constants, name.upper(), None)
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


def handle(f, url=None):
    """Return the original or wrapped file handle depending on the file name."""
    if url and url.endswith("bz2"):
        return bz2.BZ2File(f, mode="rb")
    else:
        return f


class Data(threading.Thread):
    daemon = True

    def __init__(self, *args, **kwargs):
        self.updated = threading.Event()
        self.lock = threading.Lock()

        # Get cache directory and file paths
        self.cache_dir = get_cache_dir()
        self.cache_db_path = os.path.join(self.cache_dir, CACHE_DB_NAME)
        self.cache_hash_path = os.path.join(self.cache_dir, CACHE_HASH_NAME)

        # Initialize with embedded DB path and hash
        self.actual_db_path = get_parameter("db_path")
        self.actual_db_hash = constants.DB_HASH

        # Check if user explicitly set a custom DB path (via builtins or envvar, not defaults)
        custom_db_path = getattr(builtins, "sc_data_db_path", None) or os.environ.get(
            "SC_DATA_DB_PATH"
        )

        # Try to use cached database if available and not stale
        if not custom_db_path:  # Only use cache if DB_PATH not explicitly set by user
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

    def _atomic_write_cache(self, content_source, db_hash):
        """
        Atomically write the database to cache.
        Writes to a temp file first, then renames on success.
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

            # Write database to temp file
            with os.fdopen(temp_db_fd, "wb") as temp_db_file:
                fh = handle(content_source, url=get_parameter("db_url"))
                shutil.copyfileobj(fh, temp_db_file)

            # Write hash to temp file
            with os.fdopen(temp_hash_fd, "w") as temp_hash_file:
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
                get_parameter("db_url"),
                timeout=float(get_parameter("http_timeout")),
                stream=True,
            )

            # Check for successful response
            if not (200 <= r.status_code < 300):
                logger.warning("Failed to fetch database: HTTP %d", r.status_code)
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

            need_download = (
                remote_hash != self.actual_db_hash
                or remote_hash != cached_hash
                or cache_stale
            )

            if not need_download:
                logger.debug("No need to update database (hash matches, cache fresh)")
                return True

            logger.debug(
                "Downloading new SQLite database (remote_hash=%s, cached_hash=%s, stale=%s)",
                remote_hash,
                cached_hash,
                cache_stale,
            )

            # Download and write to cache atomically
            if self._atomic_write_cache(r.raw, remote_hash):
                with self.lock:
                    self.actual_db_path = self.cache_db_path
                    self.actual_db_hash = remote_hash
                logger.debug("Updated database to hash %s", remote_hash)
                return True
            else:
                # Cache write failed, but we might still be able to use the embedded DB
                logger.warning("Cache write failed, using existing database")
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
                # After max retries, signal anyway to avoid blocking forever
                logger.warning(
                    "Failed to update database after %d attempts, using existing database",
                    max_retries + 1,
                )
                self.updated.set()
                first_attempt = False

            time.sleep(int(get_parameter("db_refresh_seconds")))
