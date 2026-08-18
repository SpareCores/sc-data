import sqlite3

import pytest

from sc_data_cli import main


def test_download(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "sc-data.db"
    monkeypatch.setattr("sys.argv", ["sc-data", "download", str(target)])
    main()
    assert target.exists()
    conn = sqlite3.connect(target)
    try:
        servers = conn.execute("SELECT COUNT(*) FROM server").fetchone()[0]
    finally:
        conn.close()
    assert servers > 0


@pytest.mark.parametrize("argv", [["sc-data"], ["sc-data", "download"]])
def test_missing_arguments(argv, monkeypatch):
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        main()
    assert excinfo.value.code == 2
