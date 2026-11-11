from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import (
    AnalyticsBase, BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory,
    DimCustomer, DimDate, DimFilm, DimStore, FactPayment, FactRental, SyncState,
)
from src.database import get_source_engine, get_target_engine
from src.source_models import (
    Actor, Address, Category, City, Country, Customer, Film, FilmActor,
    FilmCategory, Inventory, Language, Payment, Rental, Staff, Store,
)
from src.validation import validate_sessions

SOURCE_TABLES = {
    "language": Language, "film": Film, "actor": Actor, "category": Category,
    "film_actor": FilmActor, "film_category": FilmCategory, "country": Country,
    "city": City, "address": Address, "store": Store, "staff": Staff,
    "customer": Customer, "inventory": Inventory, "rental": Rental,
    "payment": Payment,
}


def init(test=False):
    """Verify MySQL and create the SQLite schema if it does not exist."""
    source_engine = get_source_engine(test)
    target_engine = get_target_engine(test)
    try:
        with Session(source_engine) as source:
            source.scalars(select(Film.film_id).limit(1)).first()
        AnalyticsBase.metadata.create_all(target_engine)
    finally:
        source_engine.dispose()
        target_engine.dispose()


def _related_ids(source, id_column, foreign_key, ids):
    if not ids:
        return set()
    return set(source.scalars(select(id_column).where(foreign_key.in_(ids))))


def _fetch(source, model, key, ids):
    if not ids:
        return []
    return source.scalars(select(model).where(key.in_(ids))).all()


def _upsert(target, model, key_name, values_by_id):
    if not values_by_id:
        return
    key_column = getattr(model, key_name)
    existing = {
        getattr(row, key_name): row
        for row in target.scalars(select(model).where(key_column.in_(values_by_id)))
    }
    for key, values in values_by_id.items():
        if key in existing:
            for name, value in values.items():
                setattr(existing[key], name, value)
        else:
            target.add(model(**{key_name: key, **values}))


def _keys(target, model, natural, surrogate, ids):
    if not ids:
        return {}
    return dict(target.execute(
        select(getattr(model, natural), getattr(model, surrogate))
        .where(getattr(model, natural).in_(ids))
    ))


def _date_key(timestamp):
    return int(timestamp.strftime("%Y%m%d"))


def _add_dates(target, timestamps):
    unique = {timestamp.date() for timestamp in timestamps if timestamp is not None}
    if not unique:
        return
    existing = set(target.scalars(select(DimDate.date_key).where(
        DimDate.date_key.in_({_date_key(day) for day in unique})
    )))
    for day in unique:
        key = _date_key(day)
        if key not in existing:
            weekday = day.isoweekday()
            target.add(DimDate(
                date_key=key, date=day, year=day.year,
                quarter=(day.month - 1) // 3 + 1, month=day.month,
                day_of_month=day.day, day_of_week=weekday,
                is_weekend=int(weekday >= 6),
            ))


def _source_changes(source, markers, full):
    changes, latest_markers = {}, {}
    for name, model in SOURCE_TABLES.items():
        latest = source.scalar(select(func.max(model.last_update)))
        previous = markers.get(name)
        latest_markers[name] = max(
            (stamp for stamp in (latest, previous) if stamp is not None),
            default=datetime(1970, 1, 1),
        )
        statement = select(model)
        if not full:
            statement = statement.where(
                model.last_update >= previous,
                model.last_update <= latest_markers[name],
            )
        changes[name] = source.scalars(statement).all()
    return changes, latest_markers


def _sync(source, target, changes, latest_markers, states):
    changed = {
        name: {getattr(row, model.__mapper__.primary_key[0].key) for row in changes[name]}
        for name, model in SOURCE_TABLES.items()
        if name not in ("film_actor", "film_category")
    }

    # Changes to location or language tables affect derived dimensions.
    cities = changed["city"] | _related_ids(
        source, City.city_id, City.country_id, changed["country"]
    )
    addresses = changed["address"] | _related_ids(
        source, Address.address_id, Address.city_id, cities
    )
    store_ids = changed["store"] | _related_ids(
        source, Store.store_id, Store.address_id, addresses
    )
    customer_ids = changed["customer"] | _related_ids(
        source, Customer.customer_id, Customer.address_id, addresses
    )
    film_ids = changed["film"] | _related_ids(
        source, Film.film_id, Film.language_id, changed["language"]
    )

    films = _fetch(source, Film, Film.film_id, film_ids)
    stores = _fetch(source, Store, Store.store_id, store_ids)
    customers = _fetch(source, Customer, Customer.customer_id, customer_ids)

    _upsert(target, DimFilm, "film_id", {
        row.film_id: dict(
            title=row.title, rating=row.rating, length=row.length,
            language=row.language.name, release_year=row.release_year,
            last_update=max(row.last_update, row.language.last_update),
        ) for row in films
    })
    _upsert(target, DimActor, "actor_id", {
        row.actor_id: dict(
            first_name=row.first_name, last_name=row.last_name,
            last_update=row.last_update,
        ) for row in changes["actor"]
    })
    _upsert(target, DimCategory, "category_id", {
        row.category_id: dict(name=row.name, last_update=row.last_update)
        for row in changes["category"]
    })
    _upsert(target, DimStore, "store_id", {
        row.store_id: dict(
            city=row.address.city_ref.city,
            country=row.address.city_ref.country_ref.country,
            last_update=max(
                row.last_update, row.address.last_update,
                row.address.city_ref.last_update,
                row.address.city_ref.country_ref.last_update,
            ),
        ) for row in stores
    })
    _upsert(target, DimCustomer, "customer_id", {
        row.customer_id: dict(
            first_name=row.first_name, last_name=row.last_name,
            active=row.active, city=row.address.city_ref.city,
            country=row.address.city_ref.country_ref.country,
            last_update=max(
                row.last_update, row.address.last_update,
                row.address.city_ref.last_update,
                row.address.city_ref.country_ref.last_update,
            ),
        ) for row in customers
    })
    target.flush()

    # Bridges use target surrogate keys; natural-key pairs remain unique.
    film_actors = changes["film_actor"]
    film_categories = changes["film_category"]
    all_bridge_film_ids = (
        {row.film_id for row in film_actors}
        | {row.film_id for row in film_categories}
    )
    film_keys = _keys(target, DimFilm, "film_id", "film_key", all_bridge_film_ids)
    actor_keys = _keys(target, DimActor, "actor_id", "actor_key",
                       {row.actor_id for row in film_actors})
    category_keys = _keys(target, DimCategory, "category_id", "category_key",
                          {row.category_id for row in film_categories})

    existing = set(target.execute(select(
        BridgeFilmActor.film_key, BridgeFilmActor.actor_key
    )))
    for row in film_actors:
        pair = (film_keys[row.film_id], actor_keys[row.actor_id])
        if pair not in existing:
            target.add(BridgeFilmActor(film_key=pair[0], actor_key=pair[1]))
            existing.add(pair)

    existing = set(target.execute(select(
        BridgeFilmCategory.film_key, BridgeFilmCategory.category_key
    )))
    for row in film_categories:
        pair = (film_keys[row.film_id], category_keys[row.category_id])
        if pair not in existing:
            target.add(BridgeFilmCategory(film_key=pair[0], category_key=pair[1]))
            existing.add(pair)

    # Inventory changes affect rentals and payments that reference that copy.
    rental_ids = changed["rental"] | _related_ids(
        source, Rental.rental_id, Rental.inventory_id, changed["inventory"]
    )
    payment_ids = changed["payment"] | _related_ids(
        source, Payment.payment_id, Payment.rental_id, rental_ids
    )
    payment_ids |= _related_ids(
        source, Payment.payment_id, Payment.staff_id, changed["staff"]
    )
    rentals = _fetch(source, Rental, Rental.rental_id, rental_ids)
    payments = _fetch(source, Payment, Payment.payment_id, payment_ids)

    films_for_facts = {row.inventory.film_id for row in rentals}
    stores_for_facts = {row.inventory.store_id for row in rentals}
    customers_for_facts = (
        {row.customer_id for row in rentals}
        | {row.customer_id for row in payments}
    )
    for payment in payments:
        stores_for_facts.add(
            payment.rental.inventory.store_id
            if payment.rental_id is not None else payment.staff.store_id
        )

    fact_films = _keys(target, DimFilm, "film_id", "film_key", films_for_facts)
    fact_stores = _keys(target, DimStore, "store_id", "store_key", stores_for_facts)
    fact_customers = _keys(
        target, DimCustomer, "customer_id", "customer_key", customers_for_facts
    )
    _add_dates(target, (
        [row.rental_date for row in rentals]
        + [row.return_date for row in rentals]
        + [row.payment_date for row in payments]
    ))
    target.flush()

    _upsert(target, FactRental, "rental_id", {
        row.rental_id: dict(
            date_key_rented=_date_key(row.rental_date),
            date_key_returned=(
                _date_key(row.return_date) if row.return_date else None
            ),
            film_key=fact_films[row.inventory.film_id],
            store_key=fact_stores[row.inventory.store_id],
            customer_key=fact_customers[row.customer_id],
            staff_id=row.staff_id,
            rental_duration_days=(
                (row.return_date.date() - row.rental_date.date()).days
                if row.return_date else None
            ),
        ) for row in rentals
    })
    _upsert(target, FactPayment, "payment_id", {
        row.payment_id: dict(
            date_key_paid=_date_key(row.payment_date),
            customer_key=fact_customers[row.customer_id],
            store_key=fact_stores[
                row.rental.inventory.store_id
                if row.rental_id is not None else row.staff.store_id
            ],
            staff_id=row.staff_id, amount=row.amount,
        ) for row in payments
    })

    for name, marker in latest_markers.items():
        if name in states:
            states[name].last_update = marker
        else:
            target.add(SyncState(table_name=name, last_update=marker))

    return {name: len(rows) for name, rows in changes.items()}


def _run(full=False, test=False):
    source_engine = get_source_engine(test)
    target_engine = get_target_engine(test)
    try:
        with Session(source_engine, autoflush=False) as source, Session(
            target_engine, autoflush=False
        ) as target:
            with target.begin():
                states = {
                    state.table_name: state
                    for state in target.scalars(select(SyncState))
                }
                if not full and set(states) != set(SOURCE_TABLES):
                    raise ValueError("Run full-load before incremental synchronization")
                markers = {name: state.last_update for name, state in states.items()}
                changes, latest_markers = _source_changes(source, markers, full)
                counts = _sync(source, target, changes, latest_markers, states)
                # Validate before COMMIT. A mismatch rolls back data and markers.
                validate_sessions(source, target)
                return counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def full_load(test=False):
    """Load all source rows and validate before committing them."""
    return _run(full=True, test=test)


def incremental(test=False):
    """Upsert changed source rows and validate before committing them."""
    return _run(full=False, test=test)
