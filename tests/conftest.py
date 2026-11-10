import os
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import (
    PROJECT_ROOT,
    get_source_engine,
)
from src.source_models import Film


@pytest.fixture
def test_db():
    """Prepare isolated databases before each test."""
    source_name = os.environ["MYSQL_DATABASE"]
    test_name = os.environ["MYSQL_TEST_DATABASE"]

    if source_name == test_name:
        raise ValueError(
            "The test MySQL database must differ from the source database."
        )

    sqlite_path = Path(
        os.environ["SQLITE_TEST_DATABASE_PATH"]
    ).expanduser()

    if not sqlite_path.is_absolute():
        sqlite_path = PROJECT_ROOT / sqlite_path

    production_path = Path(
        os.environ["SQLITE_DATABASE_PATH"]
    ).expanduser()

    if not production_path.is_absolute():
        production_path = PROJECT_ROOT / production_path

    if sqlite_path.resolve() == production_path.resolve():
        raise ValueError(
            "The test SQLite database must differ from the production database."
        )

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    if sqlite_path.exists():
        sqlite_path.unlink()

    engine = get_source_engine(test=True)

    try:
        with Session(engine) as session:
            film_count = session.scalar(
                select(func.count()).select_from(Film)
            )

            if film_count == 0:
                raise ValueError(
                    "sakila_test must contain the Sakila sample data."
                )
    finally:
        engine.dispose()

    yield


class SourceEditor:
    """Track test changes so they can be undone afterward."""

    def __init__(self, session):
        self.session = session
        self.inserted = []
        self.updated = {}

    def add(self, model, **values):
        """Add a source record and remember it for cleanup."""
        obj = model(**values)
        self.session.add(obj)
        self.inserted.append(obj)
        return obj

    def update(self, obj, **values):
        """Update a record while preserving its original values."""
        if obj not in self.updated:
            self.updated[obj] = {}

        for name, value in values.items():
            if name not in self.updated[obj]:
                self.updated[obj][name] = getattr(obj, name)

            setattr(obj, name, value)

        return obj

    def commit(self):
        """Commit changes so the sync process can read them."""
        self.session.commit()

    def restore(self):
        """Undo updates and remove inserted test records."""
        self.session.rollback()

        for obj, original_values in self.updated.items():
            for name, value in original_values.items():
                setattr(obj, name, value)

        self.session.flush()

        # Delete children before parents by reversing insertion order.
        for obj in reversed(self.inserted):
            self.session.delete(obj)

        self.session.commit()


@pytest.fixture
def source_editor(test_db):
    """Provide a source session with automatic test-data cleanup."""
    engine = get_source_engine(test=True)

    with Session(engine, expire_on_commit=False) as session:
        editor = SourceEditor(session)

        try:
            yield editor
        finally:
            try:
                editor.restore()
            finally:
                engine.dispose()
