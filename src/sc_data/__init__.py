"""
Hold/update the data file for Spare Cores.

Usage:

```
import sc_data
sc_data.db.path  # this will hold the actual data file's path (either the built-in or a temporary file when update is not disabled
sc_data.db.hash  # an SHA256 hash of the DB, can be used to track changes and reopen the database if needed
```

The module accepts the following parameters (must be set before importing):
    - builtins.sc_data_no_update / SC_DATA_NO_UPDATE - don't do updates if set
    - builtins.sc_data_db_path / SC_DATA_DB_PATH - the initial database path
    - builtins.sc_data_db_url / SC_DATA_DB_URL - DB URL to fetch updates
    - builtins.sc_data_http_timeout / SC_DATA_HTTP_TIMEOUT - HTTP timeout in seconds
    - builtins.sc_data_db_refresh_seconds / SC_DATA_DB_REFRESH_SECONDS - update database after this has passed
"""

import safe_exit

from .data import Data, close_tmpfiles, get_parameter

db = Data(name="remote_update")
db.start()
if not get_parameter("no_update"):
    safe_exit.register(close_tmpfiles, db.tmpfiles)
db.updated.wait(get_parameter("http_timeout") + 1)
