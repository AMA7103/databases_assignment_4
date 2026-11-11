import os
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from src.database import PROJECT_ROOT, get_source_engine
from src.source_models import Film


@pytest.fixture
def test_db():
    """Use a separate Sakila copy and recreate the SQLite test file."""
    if os.environ["MYSQL_DATABASE"] == os.environ["MYSQL_TEST_DATABASE"]:
        raise ValueError("MySQL test and production databases must be different")

    def path(name):
        value = Path(os.environ[name]).expanduser()
        return value if value.is_absolute() else PROJECT_ROOT / value

    test_path = path("SQLITE_TEST_DATABASE_PATH")
    if test_path.resolve() == path("SQLITE_DATABASE_PATH").resolve():
        raise ValueError("SQLite test and production databases must be different")
    test_path.parent.mkdir(parents=True, exist_ok=True)
    if test_path.exists():
        test_path.unlink()

    engine = get_source_engine(test=True)
    try:
        with Session(engine) as session:
            if not session.scalar(select(func.count()).select_from(Film)):
                raise ValueError("sakila_test needs a populated Sakila sample database")
    finally:
        engine.dispose()
    yield


class SourceEditor:
    """Track committed test inserts and changes so cleanup can reverse them."""

    def __init__(self, session):
        self.session = session
        self.inserted = []
        self.updated = {}

    def add(self, model, **values):
        obj = model(**values)
        self.session.add(obj)
        self.inserted.append(obj)
        return obj

    def update(self, obj, **values):
        original = self.updated.setdefault(obj, {})
        for name, value in values.items():
            original.setdefault(name, getattr(obj, name))
            setattr(obj, name, value)
        return obj

    def commit(self):
        self.session.commit()

    def restore(self):
        self.session.rollback()
        for obj, old_values in self.updated.items():
            for name, value in old_values.items():
                setattr(obj, name, value)
        self.session.flush()
        for obj in reversed(self.inserted):
            if inspect(obj).persistent:
                self.session.delete(obj)
        self.session.commit()


@pytest.fixture
def source_editor(test_db):
    """Restore ORM-tracked MySQL changes after each incremental test."""
    engine = get_source_engine(test=True)
    try:
        with Session(engine, expire_on_commit=False) as session:
            editor = SourceEditor(session)
            try:
                yield editor
            finally:
                editor.restore()
    finally:
        engine.dispose()
