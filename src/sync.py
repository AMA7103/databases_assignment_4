from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import (
    AnalyticsBase,
    BridgeFilmActor,
    BridgeFilmCategory,
    DimActor,
    DimCategory,
    DimCustomer,
    DimDate,
    DimFilm,
    DimStore,
    FactPayment,
    FactRental,
    SyncState,
)
from src.database import get_source_engine, get_target_engine
from src.source_models import (
    Actor,
    Address,
    Category,
    City,
    Country,
    Customer,
    Film,
    FilmActor,
    FilmCategory,
    Inventory,
    Language,
    Payment,
    Rental,
    Staff,
    Store,
)


SOURCE_TABLES = {
    "language": Language,
    "film": Film,
    "actor": Actor,
    "category": Category,
    "film_actor": FilmActor,
    "film_category": FilmCategory,
    "country": Country,
    "city": City,
    "address": Address,
    "store": Store,
    "staff": Staff,
    "customer": Customer,
    "inventory": Inventory,
    "rental": Rental,
    "payment": Payment,
}


def init(test=False):
    """Verify MySQL connectivity and create SQLite analytics tables."""
    source_engine = get_source_engine(test)
    target_engine = get_target_engine(test)

    try:
        with Session(source_engine) as source:
            source.scalars(select(Film.film_id).limit(1)).first()

        AnalyticsBase.metadata.create_all(target_engine)
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _select_ids(source, column, filter_column, values):
    """Find IDs of rows whose foreign key matches any supplied ID."""
    if not values:
        return set()

    return set(
        source.scalars(
            select(column).where(filter_column.in_(values))
        ).all()
    )


def _fetch(source, model, key, ids):
    """Load ORM objects for the specified primary keys."""
    if not ids:
        return []

    return source.scalars(
        select(model).where(key.in_(ids))
    ).all()


def _upsert_rows(target, model, natural_key, rows):
    """Insert or update ORM objects using their natural keys."""
    if not rows:
        return

    column = getattr(model, natural_key)

    existing = {
        getattr(obj, natural_key): obj
        for obj in target.scalars(
            select(model).where(column.in_(list(rows)))
        )
    }

    for key, values in rows.items():
        obj = existing.get(key)

        if obj is None:
            target.add(model(**{natural_key: key, **values}))
        else:
            for attribute, value in values.items():
                setattr(obj, attribute, value)


def _dimension_keys(target, model, natural_key, surrogate_key, ids):
    """Map source natural keys to SQLite surrogate keys."""
    if not ids:
        return {}

    rows = target.execute(
        select(
            getattr(model, natural_key),
            getattr(model, surrogate_key),
        ).where(getattr(model, natural_key).in_(ids))
    )

    return dict(rows)


def _date_fields(timestamp):
    """Convert a timestamp into a YYYYMMDD key and Python date."""
    day = timestamp.date()
    return int(day.strftime("%Y%m%d")), day


def _ensure_dates(target, timestamps):
    """Insert any missing dates into the date dimension."""
    dates = {
        _date_fields(value)
        for value in timestamps
        if value is not None
    }

    if not dates:
        return

    existing = set(
        target.scalars(
            select(DimDate.date_key).where(
                DimDate.date_key.in_([key for key, _ in dates])
            )
        )
    )

    for key, day in dates:
        if key in existing:
            continue

        weekday = day.isoweekday()

        target.add(
            DimDate(
                date_key=key,
                date=day,
                year=day.year,
                quarter=(day.month - 1) // 3 + 1,
                month=day.month,
                day_of_month=day.day,
                day_of_week=weekday,
                is_weekend=int(weekday >= 6),
            )
        )


def _changed(source, markers, full):
    """Read all source rows or only rows changed since each marker."""
    changes = {}
    maximums = {}

    for name, model in SOURCE_TABLES.items():
        latest = source.scalar(
            select(func.max(model.last_update))
        )

        previous = markers.get(name)

        maximums[name] = max(
            (
                stamp
                for stamp in (latest, previous)
                if stamp is not None
            ),
            default=datetime(1970, 1, 1),
        )

        statement = select(model)

        if not full:
            statement = statement.where(
                model.last_update >= previous,
                model.last_update <= maximums[name],
            )

        changes[name] = source.scalars(statement).all()

    return changes, maximums


def _sync(source, target, changes, maximums, states):
    """Transform and upsert dimensions, bridges, facts, and markers."""

    changed = {
        name: {
            getattr(row, list(model.__table__.primary_key.columns)[0].name)
            for row in changes[name]
        }
        for name, model in SOURCE_TABLES.items()
        if name not in ("film_actor", "film_category")
    }

    # Identify dimensions affected by related-table changes.
    city_ids = changed["city"] | _select_ids(
        source, City.city_id, City.country_id, changed["country"]
    )

    address_ids = changed["address"] | _select_ids(
        source, Address.address_id, Address.city_id, city_ids
    )

    store_ids = changed["store"] | _select_ids(
        source, Store.store_id, Store.address_id, address_ids
    )

    customer_ids = changed["customer"] | _select_ids(
        source, Customer.customer_id, Customer.address_id, address_ids
    )

    film_ids = changed["film"] | _select_ids(
        source, Film.film_id, Film.language_id, changed["language"]
    )

    films = _fetch(source, Film, Film.film_id, film_ids)
    actors = changes["actor"]
    categories = changes["category"]
    stores = _fetch(source, Store, Store.store_id, store_ids)
    customers = _fetch(
        source, Customer, Customer.customer_id, customer_ids
    )

    # Load dimensions first.
    _upsert_rows(
        target, DimFilm, "film_id",
        {
            film.film_id: {
                "title": film.title,
                "rating": film.rating,
                "length": film.length,
                "language": film.language.name,
                "release_year": film.release_year,
                "last_update": max(
                    film.last_update, film.language.last_update
                ),
            }
            for film in films
        },
    )

    _upsert_rows(
        target, DimActor, "actor_id",
        {
            actor.actor_id: {
                "first_name": actor.first_name,
                "last_name": actor.last_name,
                "last_update": actor.last_update,
            }
            for actor in actors
        },
    )

    _upsert_rows(
        target, DimCategory, "category_id",
        {
            category.category_id: {
                "name": category.name,
                "last_update": category.last_update,
            }
            for category in categories
        },
    )

    _upsert_rows(
        target, DimStore, "store_id",
        {
            store.store_id: {
                "city": store.address.city_ref.city,
                "country": store.address.city_ref.country_ref.country,
                "last_update": max(
                    store.last_update,
                    store.address.last_update,
                    store.address.city_ref.last_update,
                    store.address.city_ref.country_ref.last_update,
                ),
            }
            for store in stores
        },
    )

    _upsert_rows(
        target, DimCustomer, "customer_id",
        {
            customer.customer_id: {
                "first_name": customer.first_name,
                "last_name": customer.last_name,
                "active": customer.active,
                "city": customer.address.city_ref.city,
                "country": customer.address.city_ref.country_ref.country,
                "last_update": max(
                    customer.last_update,
                    customer.address.last_update,
                    customer.address.city_ref.last_update,
                    customer.address.city_ref.country_ref.last_update,
                ),
            }
            for customer in customers
        },
    )

    target.flush()

    # Load many-to-many bridges after dimensions.
    film_actor_rows = changes["film_actor"]
    film_category_rows = changes["film_category"]

    bridge_film_ids = {
        row.film_id for row in film_actor_rows
    } | {
        row.film_id for row in film_category_rows
    }

    film_keys = _dimension_keys(
        target, DimFilm, "film_id", "film_key", bridge_film_ids
    )

    actor_keys = _dimension_keys(
        target, DimActor, "actor_id", "actor_key",
        {row.actor_id for row in film_actor_rows},
    )

    category_keys = _dimension_keys(
        target, DimCategory, "category_id", "category_key",
        {row.category_id for row in film_category_rows},
    )

    existing_actor_bridges = set(
        target.execute(
            select(
                BridgeFilmActor.film_key,
                BridgeFilmActor.actor_key,
            )
        )
    )

    for row in film_actor_rows:
        pair = (
            film_keys[row.film_id],
            actor_keys[row.actor_id],
        )

        if pair not in existing_actor_bridges:
            target.add(
                BridgeFilmActor(
                    film_key=pair[0],
                    actor_key=pair[1],
                )
            )
            existing_actor_bridges.add(pair)

    existing_category_bridges = set(
        target.execute(
            select(
                BridgeFilmCategory.film_key,
                BridgeFilmCategory.category_key,
            )
        )
    )

    for row in film_category_rows:
        pair = (
            film_keys[row.film_id],
            category_keys[row.category_id],
        )

        if pair not in existing_category_bridges:
            target.add(
                BridgeFilmCategory(
                    film_key=pair[0],
                    category_key=pair[1],
                )
            )
            existing_category_bridges.add(pair)

    # Inventory changes can affect rentals and payments.
    rental_ids = changed["rental"] | _select_ids(
        source,
        Rental.rental_id,
        Rental.inventory_id,
        changed["inventory"],
    )

    payment_ids = changed["payment"] | _select_ids(
        source,
        Payment.payment_id,
        Payment.rental_id,
        rental_ids,
    )

    # Staff changes can affect payments without rentals.
    payment_ids |= _select_ids(
        source,
        Payment.payment_id,
        Payment.staff_id,
        changed["staff"],
    )

    rentals = _fetch(
        source, Rental, Rental.rental_id, rental_ids
    )

    payments = _fetch(
        source, Payment, Payment.payment_id, payment_ids
    )

    # Resolve the dimension keys required by the facts.
    fact_film_ids = {
        rental.inventory.film_id for rental in rentals
    }

    fact_store_ids = {
        rental.inventory.store_id for rental in rentals
    }

    fact_customer_ids = {
        rental.customer_id for rental in rentals
    } | {
        payment.customer_id for payment in payments
    }

    for payment in payments:
        if payment.rental_id is not None:
            fact_store_ids.add(
                payment.rental.inventory.store_id
            )
        else:
            fact_store_ids.add(payment.staff.store_id)

    fact_film_keys = _dimension_keys(
        target, DimFilm, "film_id", "film_key", fact_film_ids
    )

    fact_store_keys = _dimension_keys(
        target, DimStore, "store_id", "store_key", fact_store_ids
    )

    fact_customer_keys = _dimension_keys(
        target,
        DimCustomer,
        "customer_id",
        "customer_key",
        fact_customer_ids,
    )

    # Ensure all rental, return, and payment dates exist.
    _ensure_dates(
        target,
        [rental.rental_date for rental in rentals]
        + [rental.return_date for rental in rentals]
        + [payment.payment_date for payment in payments],
    )

    target.flush()

    # Load rental facts.
    _upsert_rows(
        target, FactRental, "rental_id",
        {
            rental.rental_id: {
                "date_key_rented": _date_fields(
                    rental.rental_date
                )[0],
                "date_key_returned": (
                    _date_fields(rental.return_date)[0]
                    if rental.return_date is not None
                    else None
                ),
                "film_key": fact_film_keys[
                    rental.inventory.film_id
                ],
                "store_key": fact_store_keys[
                    rental.inventory.store_id
                ],
                "customer_key": fact_customer_keys[
                    rental.customer_id
                ],
                "staff_id": rental.staff_id,
                "rental_duration_days": (
                    (
                        rental.return_date.date()
                        - rental.rental_date.date()
                    ).days
                    if rental.return_date is not None
                    else None
                ),
            }
            for rental in rentals
        },
    )

    # Load payment facts.
    _upsert_rows(
        target, FactPayment, "payment_id",
        {
            payment.payment_id: {
                "date_key_paid": _date_fields(
                    payment.payment_date
                )[0],
                "customer_key": fact_customer_keys[
                    payment.customer_id
                ],
                "store_key": fact_store_keys[
                    payment.rental.inventory.store_id
                    if payment.rental_id is not None
                    else payment.staff.store_id
                ],
                "staff_id": payment.staff_id,
                "amount": payment.amount,
            }
            for payment in payments
        },
    )

    # Save the markers in the same SQLite transaction.
    for name, timestamp in maximums.items():
        state = states.get(name)

        if state is None:
            target.add(
                SyncState(
                    table_name=name,
                    last_update=timestamp,
                )
            )
        else:
            state.last_update = timestamp

    return {
        name: len(rows)
        for name, rows in changes.items()
    }


def _run(full=False, test=False):
    """Execute synchronization within one target transaction."""
    source_engine = get_source_engine(test)
    target_engine = get_target_engine(test)

    try:
        with Session(
            source_engine, autoflush=False
        ) as source, Session(
            target_engine, autoflush=False
        ) as target:

            with target.begin():
                states = {
                    state.table_name: state
                    for state in target.scalars(
                        select(SyncState)
                    )
                }

                if not full and set(states) != set(SOURCE_TABLES):
                    raise ValueError(
                        "Run full-load before incremental synchronization"
                    )

                markers = {
                    name: state.last_update
                    for name, state in states.items()
                }

                changes, maximums = _changed(
                    source, markers, full
                )

                return _sync(
                    source, target, changes, maximums, states
                )
    finally:
        source_engine.dispose()
        target_engine.dispose()


def full_load(test=False):
    """Synchronize all source records without duplicating rows."""
    return _run(full=True, test=test)


def incremental(test=False):
    """Synchronize rows changed since the previous successful run."""
    return _run(full=False, test=test)
