from sqlalchemy import inspect

from src.database import get_target_engine
from src.sync import init


def test_init(test_db):
    """Verify initialization creates all required analytics tables."""
    init(test=True)

    engine = get_target_engine(test=True)

    try:
        expected_tables = {
            "dim_date",
            "dim_film",
            "dim_actor",
            "dim_category",
            "dim_store",
            "dim_customer",
            "bridge_film_actor",
            "bridge_film_category",
            "fact_rental",
            "fact_payment",
            "sync_state",
        }

        actual_tables = set(inspect(engine).get_table_names())

        assert actual_tables == expected_tables

        # Initialization should be safe to run again.
        init(test=True)

        actual_tables = set(inspect(engine).get_table_names())

        assert actual_tables == expected_tables

    finally:
        engine.dispose()
