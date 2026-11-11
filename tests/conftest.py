import os
from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from src.database import PROJECT_ROOT, get_source_engine
from src.source_models import Actor, Address, Customer, Film, FilmActor, Rental

def reset_test_source():
    """Undo changes made by the current tests using clean Sakila values."""
    clean_engine = get_source_engine()
    test_engine = get_source_engine(test=True)

    try:
        with Session(clean_engine) as clean, Session(test_engine) as test:
            with test.begin():
                # Remove actors inserted by test_incremental_insert.
                original_actor_ids = set(clean.scalars(select(Actor.actor_id)))
                test_actors = test.scalars(
                    select(Actor).where(
                        Actor.first_name == "TEST",
                        Actor.last_name == "INCREMENTAL",
                    )
                ).all()

                for actor in test_actors:
                    if actor.actor_id in original_actor_ids:
                        continue
                    links = test.scalars(
                        select(FilmActor).where(
                            FilmActor.actor_id == actor.actor_id
                        )
                    ).all()
                    for link in links:
                        test.delete(link)
                    test.flush()
                    test.delete(actor)

                test.flush()

                # Restore fields changed by test_incremental_update.
                for model, key, fields in (
                    (Customer, "customer_id", ("first_name", "last_update")),
                    (Address, "address_id", ("city_id", "last_update")),
                    (Rental, "rental_id", ("return_date", "last_update")),
                ):
                    originals = {
                        getattr(row, key): row
                        for row in clean.scalars(select(model))
                    }
                    for row in test.scalars(select(model)):
                        original = originals.get(getattr(row, key))
                        if original is None:
                            continue
                        for field in fields:
                            value = getattr(original, field)
                            if getattr(row, field) != value:
                                setattr(row, field, value)
    finally:
        clean_engine.dispose()
        test_engine.dispose()

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

    reset_test_source()
    
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
