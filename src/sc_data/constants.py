import importlib.resources

PKG_DB_FILE = "data/spare_cores.db"
DB_PATH = importlib.resources.files(__name__.split(".")[0]).joinpath(PKG_DB_FILE)
# FIXME: replace with the final URL of the database
DB_URL = "https://github.com/SpareCores/sc-data/raw/main/sc_data/data/spare_cores.db"
HTTP_TIMEOUT = 5
DB_REFRESH_SECONDS = 600
