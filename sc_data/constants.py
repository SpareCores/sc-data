import importlib.resources

PKG_DB_FILE = "data/spare_cores.db"
DB_PATH = importlib.resources.files(__name__.split(".")[0]).joinpath(PKG_DB_FILE)
# FIXME: replace with the final URL of the database
DB_URL = "https://github.com/nogibjj/Sjg80-Rust-CLI-Binary-with-SQLite/raw/d35d8d9c5ecb6d80322eeeff8df6485a7f09b51b/Car_Database.db"
HTTP_TIMEOUT = 5
DB_REFRESH_SECONDS = 600
