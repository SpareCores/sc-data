from os.path import abspath, exists

from sc_data import db
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def test_has_path():
    assert db.path is not None
    assert exists(db.path)


def test_can_query_db():
    connection_string = "sqlite:///" + abspath(db.path)
    print(connection_string)
    engine = create_engine(connection_string)
    Session = sessionmaker(engine)
    with Session() as s:
        servers = s.execute(text("SELECT COUNT(*) FROM server")).fetchone()[0]
    assert servers > 0
