CDN_BASE = "https://cdn.sparecores.net/sc-data"


def _default_db_url():
    """Use crawler major.minor for versioned CDN path when available; otherwise 'latest'."""
    try:
        import sc_crawler

        v = sc_crawler.__version_info__
        version = f"{v[0]}.{v[1]}"
        return f"{CDN_BASE}/{version}/sc-data-all.sql.xz"
    except ImportError:
        return f"{CDN_BASE}/latest/sc-data-all.sql.xz"
    else:
        v = getattr(sc_crawler, "__version_info__", None)
        if isinstance(v, (tuple, list)) and len(v) >= 2:
            return f"{CDN_BASE}/{v[0]}.{v[1]}/sc-data-all.sql.xz"
        return f"{CDN_BASE}/latest/sc-data-all.sql.xz"


DB_URL = _default_db_url()
HTTP_TIMEOUT = 600
DB_REFRESH_SECONDS = 600
