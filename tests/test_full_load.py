from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import get_source_engine, get_target_engine
from src.source_models import (
    Film, Actor, Category, Store, Customer,
    FilmActor, FilmCategory, Rental, Payment,
)
from src.analytics_models import (
    DimFilm, DimActor, DimCategory, DimStore, DimCustomer,
    BridgeFilmActor, BridgeFilmCategory,
    FactRental, FactPayment, SyncState,
)
from src.sync import init, full_load


def test_full_load(test_db):
    """Verify full load copies all data and is safe to rerun."""
    init(test=True)
    full_load(test=True)

    source_engine = get_source_engine(test=True)
    target_engine = get_target_engine(test=True)

    try:
        with Session(source_engine) as source, Session(target_engine) as target:
            tables = [
                (Film, DimFilm),
                (Actor, DimActor),
                (Category, DimCategory),
                (Store, DimStore),
                (Customer, DimCustomer),
                (FilmActor, BridgeFilmActor),
                (FilmCategory, BridgeFilmCategory),
                (Rental, FactRental),
                (Payment, FactPayment),
            ]

            # All source and target table counts must match.
            for source_model, target_model in tables:
                source_count = source.scalar(
                    select(func.count()).select_from(source_model)
                )
                target_count = target.scalar(
                    select(func.count()).select_from(target_model)
                )

                assert source_count == target_count, (
                    f"Row count mismatch for {target_model.__tablename__}"
                )

            # A synchronization marker must exist for each source table.
            marker_count = target.scalar(
                select(func.count()).select_from(SyncState)
            )
            assert marker_count == 15

            # Record counts and surrogate keys before rerunning.
            original_counts = {
                model.__tablename__: target.scalar(
                    select(func.count()).select_from(model)
                )
                for _, model in tables
            }

            original_film_keys = dict(
                target.execute(
                    select(DimFilm.film_id, DimFilm.film_key)
                )
            )

        # Running the full load again must not create duplicates.
        full_load(test=True)

        with Session(target_engine) as target:
            for _, model in tables:
                new_count = target.scalar(
                    select(func.count()).select_from(model)
                )

                assert new_count == original_counts[model.__tablename__]

            new_film_keys = dict(
                target.execute(
                    select(DimFilm.film_id, DimFilm.film_key)
                )
            )

            # Existing dimension records retain their surrogate keys.
            assert new_film_keys == original_film_keys

            marker_count = target.scalar(
                select(func.count()).select_from(SyncState)
            )
            assert marker_count == 15

    finally:
        source_engine.dispose()
        target_engine.dispose()
