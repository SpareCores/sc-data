"""
Hold/update the data file for Spare Cores.

Usage:

```
import sc_data
sc_data.db.path  # this will hold the actual data file's path (either the built-in, cached, or a custom path)
sc_data.db.hash  # an SHA256 hash of the DB, can be used to track changes and reopen the database if needed
```

The module accepts the following parameters (must be set before importing):
    - builtins.sc_data_no_update / SC_DATA_NO_UPDATE - don't do updates if set
    - builtins.sc_data_db_path / SC_DATA_DB_PATH - the initial database path (overrides cache)
    - builtins.sc_data_db_url / SC_DATA_DB_URL - DB URL to fetch updates
    - builtins.sc_data_http_timeout / SC_DATA_HTTP_TIMEOUT - HTTP timeout in seconds
    - builtins.sc_data_db_refresh_seconds / SC_DATA_DB_REFRESH_SECONDS - update database after this has passed
    - builtins.sc_data_db_cache_ttl / SC_DATA_DB_CACHE_TTL - cache TTL in seconds (default: 86400 = 1 day)

Cache location (when SC_DATA_DB_PATH is not set):
    - Linux: $XDG_CACHE_HOME/sparecores-data/ or ~/.cache/sparecores-data/
    - macOS: ~/Library/Caches/sparecores-data/
    - Windows: %LOCALAPPDATA%/sparecores-data/
"""

from .data import Data, get_parameter

db = Data(name="remote_update")
db.start()
db.updated.wait(float(get_parameter("http_timeout")) + 1)
