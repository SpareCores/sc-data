import importlib.resources

PKG_DB_FILE = "data/sc-data-all.db"
DB_PATH = importlib.resources.files(__name__.split(".")[0]).joinpath(PKG_DB_FILE)
# FIXME: replace with the final URL of the database
DB_URL = "https://sc-data-public-40e9d310.s3.amazonaws.com/sc-data-all.db"
HTTP_TIMEOUT = 5
DB_REFRESH_SECONDS = 600
