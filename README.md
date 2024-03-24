## Spare Cores Data

SC Data is a Python package and related tools making use of
[SC Crawler](https://github.com/SpareCores/sc-crawler) to pull and
standardize data on cloud compute resources. This repository actually
runs the crawler every 5 minutes to update spot prices, and every hour
to update all cloud resources in an internal SCD table and public
SQLite snapshot as well.

For easy access to the SQLite database file, import the `db` object
of the `sc_data` Python package, which runs an updater thread in the
background to keep the SQLite file up-to-date:

```py
from sc_data import db
print(db.path)
```

By default, the SQLite file will be updated every 600 seconds, which
can be overwritten by the `sc_data_db_refresh_seconds` builtins
attribute or the `SC_DATA_DB_REFRESH_SECONDS` environment variable.

References:

- [SC Crawler documentation](https://sparecores.github.io/sc-crawler/)
- [Database schemas](https://dbdocs.io/spare-cores/sc-crawler)
- [Latest SQLite database release](https://sc-data-public-40e9d310.s3.amazonaws.com/sc-data-all.db.bz2)
- [sparecores.com](https://sparecores.com)
