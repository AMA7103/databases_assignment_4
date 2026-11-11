from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import (
    BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory, DimCustomer,
    DimFilm, DimStore, FactPayment, FactRental, SyncState,
)
from src.database import get_source_engine, get_target_engine
from src.source_models import (
    Actor, Category, Customer, Film, FilmActor, FilmCategory,
    Payment, Rental, Store,
)
from src.sync import full_load, init
from src.validation import validate


def test_full_load(test_db):
    """Check complete loads, synchronization markers, and rerun safety."""
    init(test=True)
    full_load(test=True)  # Includes automatic validation before commit.

    source_engine = get_source_engine(test=True)
    target_engine = get_target_engine(test=True)
    try:
        tables = [
            (Film, DimFilm), (Actor, DimActor), (Category, DimCategory),
            (Store, DimStore), (Customer, DimCustomer),
            (FilmActor, BridgeFilmActor), (FilmCategory, BridgeFilmCategory),
            (Rental, FactRental), (Payment, FactPayment),
        ]
        with Session(source_engine) as source, Session(target_engine) as target:
            original_counts = {}
            for original, copied in tables:
                source_count = source.scalar(select(func.count()).select_from(original))
                target_count = target.scalar(select(func.count()).select_from(copied))
                assert source_count == target_count, copied.__tablename__
                original_counts[copied.__tablename__] = target_count
            assert target.scalar(select(func.count()).select_from(SyncState)) == 15
            original_keys = dict(target.execute(select(DimFilm.film_id, DimFilm.film_key)))

        assert validate(test=True)["status"] == "passed"
        full_load(test=True)

        with Session(target_engine) as target:
            for _, copied in tables:
                assert target.scalar(
                    select(func.count()).select_from(copied)
                ) == original_counts[copied.__tablename__]
            assert dict(target.execute(
                select(DimFilm.film_id, DimFilm.film_key)
            )) == original_keys
            assert target.scalar(select(func.count()).select_from(SyncState)) == 15
    finally:
        source_engine.dispose()
        target_engine.dispose()
