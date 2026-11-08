import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def get_source_engine(test=False):
    """Create a MySQL engine for Sakila."""
    database = (
        os.environ["MYSQL_TEST_DATABASE"]
        if test
        else os.environ["MYSQL_DATABASE"]
    )

    url = URL.create(
        "mysql+pymysql",
        username=os.environ["MYSQL_USER"],
        password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        database=database,
    )

    return create_engine(url, pool_pre_ping=True)


def get_target_engine(test=False):
    """Create a SQLite engine for analytics."""
    path_var = (
        "SQLITE_TEST_DATABASE_PATH"
        if test
        else "SQLITE_DATABASE_PATH"
    )

    path = Path(os.environ[path_var]).expanduser()

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def get_source_session(test=False):
    """Create a MySQL ORM session."""
    return Session(get_source_engine(test=test), autoflush=False)


def get_target_session(test=False):
    """Create a SQLite ORM session."""
    return Session(get_target_engine(test=test), autoflush=False)
