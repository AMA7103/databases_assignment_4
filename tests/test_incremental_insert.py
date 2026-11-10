from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import (
    BridgeFilmActor,
    DimActor,
    DimFilm,
)
from src.database import get_target_engine
from src.source_models import Actor, Film, FilmActor
from src.sync import init, full_load, incremental


def test_incremental_insert(source_editor):
    """Verify new source records are inserted without duplicates."""
    init(test=True)
    full_load(test=True)

    source = source_editor.session

    # Choose an existing film for the new actor.
    film_id = source.scalar(
        select(Film.film_id).order_by(Film.film_id).limit(1)
    )

    timestamp = datetime.now().replace(microsecond=0)

    # Insert a new actor into the MySQL test database.
    actor = source_editor.add(
        Actor,
        first_name="TEST",
        last_name="INCREMENTAL",
        last_update=timestamp,
    )

    source_editor.commit()
    actor_id = actor.actor_id

    # Associate the new actor with an existing film.
    source_editor.add(
        FilmActor,
        actor_id=actor_id,
        film_id=film_id,
        last_update=timestamp,
    )

    source_editor.commit()

    # Synchronize the newly inserted records.
    incremental(test=True)

    engine = get_target_engine(test=True)

    try:
        with Session(engine) as target:
            new_actor = target.scalar(
                select(DimActor).where(
                    DimActor.actor_id == actor_id
                )
            )

            assert new_actor is not None
            assert new_actor.first_name == "TEST"
            assert new_actor.last_name == "INCREMENTAL"

            actor_key = new_actor.actor_key

            film_key = target.scalar(
                select(DimFilm.film_key).where(
                    DimFilm.film_id == film_id
                )
            )

            # Verify the many-to-many bridge was synchronized.
            bridge_count = target.scalar(
                select(func.count())
                .select_from(BridgeFilmActor)
                .where(
                    BridgeFilmActor.actor_key == actor_key,
                    BridgeFilmActor.film_key == film_key,
                )
            )

            assert bridge_count == 1

        # Rerunning incremental must not create duplicates.
        incremental(test=True)

        with Session(engine) as target:
            actor_count = target.scalar(
                select(func.count())
                .select_from(DimActor)
                .where(DimActor.actor_id == actor_id)
            )

            bridge_count = target.scalar(
                select(func.count())
                .select_from(BridgeFilmActor)
                .where(
                    BridgeFilmActor.actor_key == actor_key,
                    BridgeFilmActor.film_key == film_key,
                )
            )

            assert actor_count == 1
            assert bridge_count == 1

            # The existing surrogate key must remain unchanged.
            assert target.scalar(
                select(DimActor.actor_key).where(
                    DimActor.actor_id == actor_id
                )
            ) == actor_key

    finally:
        engine.dispose()
