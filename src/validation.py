from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

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
)
from src.database import get_source_engine, get_target_engine
from src.source_models import (
    Actor,
    Category,
    Customer,
    Film,
    FilmActor,
    FilmCategory,
    Inventory,
    Payment,
    Rental,
    Staff,
    Store,
)


def _parse_date(value):
    """Convert a date string to a Python date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _date_key(value):
    """Convert a date to its YYYYMMDD integer key."""
    return int(value.strftime("%Y%m%d"))


def _compare_keys(source, target, source_model, target_model,
                  source_key, target_key, errors):
    """Compare natural keys and row counts between two tables."""
    source_ids = set(source.scalars(
        select(getattr(source_model, source_key))
    ))
    target_ids = set(target.scalars(
        select(getattr(target_model, target_key))
    ))

    name = target_model.__tablename__

    if len(source_ids) != len(target_ids):
        errors.append(
            f"{name}: source has {len(source_ids)} rows, "
            f"target has {len(target_ids)}"
        )

    missing = source_ids - target_ids
    extra = target_ids - source_ids

    if missing:
        errors.append(f"{name}: {len(missing)} missing records")
    if extra:
        errors.append(f"{name}: {len(extra)} unexpected records")

    return len(source_ids)


def _check_duplicates(target, errors):
    """Detect duplicate natural keys in analytics tables."""
    tables = [
        (DimFilm, [DimFilm.film_id]),
        (DimActor, [DimActor.actor_id]),
        (DimCategory, [DimCategory.category_id]),
        (DimStore, [DimStore.store_id]),
        (DimCustomer, [DimCustomer.customer_id]),
        (DimDate, [DimDate.date_key]),
        (FactRental, [FactRental.rental_id]),
        (FactPayment, [FactPayment.payment_id]),
        (BridgeFilmActor, [
            BridgeFilmActor.film_key,
            BridgeFilmActor.actor_key,
        ]),
        (BridgeFilmCategory, [
            BridgeFilmCategory.film_key,
            BridgeFilmCategory.category_key,
        ]),
    ]

    for model, columns in tables:
        duplicate = target.execute(
            select(*columns)
            .group_by(*columns)
            .having(func.count() > 1)
            .limit(1)
        ).first()

        if duplicate:
            errors.append(
                f"{model.__tablename__}: duplicate keys detected"
            )


def _check_foreign_keys(target, errors):
    """Detect records referencing nonexistent dimension rows."""
    tables = [
        BridgeFilmActor,
        BridgeFilmCategory,
        FactRental,
        FactPayment,
    ]

    for model in tables:
        table = model.__table__

        for foreign_key in table.foreign_keys:
            referenced_table = foreign_key.column.table

            count = target.scalar(
                select(func.count())
                .select_from(
                    table.outerjoin(
                        referenced_table,
                        foreign_key.parent == foreign_key.column,
                    )
                )
                .where(foreign_key.column.is_(None))
            )

            if count:
                errors.append(
                    f"{table.name}.{foreign_key.parent.name}: "
                    f"{count} invalid references"
                )


def _check_bridges(source, target, errors):
    """Compare many-to-many relationships using natural keys."""
    actor_source = set(source.execute(
        select(FilmActor.film_id, FilmActor.actor_id)
    ))

    actor_target = set(target.execute(
        select(DimFilm.film_id, DimActor.actor_id)
        .select_from(BridgeFilmActor)
        .join(DimFilm, BridgeFilmActor.film_key == DimFilm.film_key)
        .join(DimActor, BridgeFilmActor.actor_key == DimActor.actor_key)
    ))

    category_source = set(source.execute(
        select(FilmCategory.film_id, FilmCategory.category_id)
    ))

    category_target = set(target.execute(
        select(DimFilm.film_id, DimCategory.category_id)
        .select_from(BridgeFilmCategory)
        .join(DimFilm, BridgeFilmCategory.film_key == DimFilm.film_key)
        .join(
            DimCategory,
            BridgeFilmCategory.category_key == DimCategory.category_key,
        )
    ))

    for name, source_pairs, target_pairs in [
        ("bridge_film_actor", actor_source, actor_target),
        ("bridge_film_category", category_source, category_target),
    ]:
        if source_pairs != target_pairs:
            errors.append(
                f"{name}: {len(source_pairs - target_pairs)} missing "
                f"and {len(target_pairs - source_pairs)} unexpected "
                "relationships"
            )


def _check_dates(source, target, errors):
    """Check that all source transaction dates exist in dim_date."""
    required = set()

    for column in [
        Rental.rental_date,
        Rental.return_date,
        Payment.payment_date,
    ]:
        dates = source.scalars(
            select(func.date(column))
            .where(column.is_not(None))
            .distinct()
        )

        for day in dates:
            required.add(_date_key(_parse_date(day)))

    existing = set(target.scalars(select(DimDate.date_key)))
    missing = required - existing

    if missing:
        errors.append(
            f"dim_date: {len(missing)} source dates are missing"
        )


def _check_payments(source, target, start, end, errors):
    """Compare payment IDs and revenue totals by store."""
    start_time = datetime.combine(start, time.min)
    end_time = datetime.combine(end + timedelta(days=1), time.min)

    source_rows = source.execute(
        select(
            Payment.payment_id,
            Payment.amount,
            func.coalesce(Inventory.store_id, Staff.store_id),
        )
        .select_from(Payment)
        .outerjoin(Rental, Payment.rental_id == Rental.rental_id)
        .outerjoin(Inventory, Rental.inventory_id == Inventory.inventory_id)
        .join(Staff, Payment.staff_id == Staff.staff_id)
        .where(
            Payment.payment_date >= start_time,
            Payment.payment_date < end_time,
        )
    ).all()

    target_rows = target.execute(
        select(
            FactPayment.payment_id,
            FactPayment.amount,
            DimStore.store_id,
        )
        .join(DimStore, FactPayment.store_key == DimStore.store_key)
        .where(
            FactPayment.date_key_paid.between(
                _date_key(start), _date_key(end)
            )
        )
    ).all()

    source_ids = {row[0] for row in source_rows}
    target_ids = {row[0] for row in target_rows}

    if len(source_rows) != len(target_rows):
        errors.append(
            f"fact_payment: source has {len(source_rows)} rows, "
            f"target has {len(target_rows)}"
        )

    if source_ids != target_ids:
        errors.append(
            f"fact_payment: {len(source_ids - target_ids)} missing "
            f"and {len(target_ids - source_ids)} unexpected payments"
        )

    source_totals = defaultdict(Decimal)
    target_totals = defaultdict(Decimal)

    for _, amount, store_id in source_rows:
        source_totals[store_id] += amount

    for _, amount, store_id in target_rows:
        target_totals[store_id] += amount

    for store_id in source_totals.keys() | target_totals.keys():
        difference = abs(
            source_totals[store_id] - target_totals[store_id]
        )

        if difference > Decimal("0.01"):
            errors.append(
                f"Store {store_id}: payment totals differ by "
                f"${difference:.2f}"
            )

    return len(source_rows)


def _check_rentals(source, target, start, end, errors):
    """Compare rental records within the selected date range."""
    start_time = datetime.combine(start, time.min)
    end_time = datetime.combine(end + timedelta(days=1), time.min)

    source_ids = set(source.scalars(
        select(Rental.rental_id).where(
            Rental.rental_date >= start_time,
            Rental.rental_date < end_time,
        )
    ))

    target_ids = set(target.scalars(
        select(FactRental.rental_id).where(
            FactRental.date_key_rented.between(
                _date_key(start), _date_key(end)
            )
        )
    ))

    if len(source_ids) != len(target_ids):
        errors.append(
            f"fact_rental: source has {len(source_ids)} rows, "
            f"target has {len(target_ids)}"
        )

    if source_ids != target_ids:
        errors.append(
            f"fact_rental: {len(source_ids - target_ids)} missing "
            f"and {len(target_ids - source_ids)} unexpected rentals"
        )

    return len(source_ids)


def validate(start_date=None, end_date=None, test=False):
    """Validate SQLite analytics data against the MySQL source."""
    source_engine = get_source_engine(test)
    target_engine = get_target_engine(test)

    try:
        with Session(source_engine) as source, Session(target_engine) as target:
            latest = source.scalar(select(func.max(Payment.payment_date)))

            if latest is None:
                raise ValueError("No payments found in Sakila")

            end = _parse_date(end_date) if end_date else latest.date()
            start = (
                _parse_date(start_date)
                if start_date
                else end - timedelta(days=29)
            )

            if start > end:
                raise ValueError("Start date cannot be after end date")

            errors = []
            counts = {}

            # Compare dimension row counts and natural keys.
            dimensions = [
                (Film, DimFilm, "film_id"),
                (Actor, DimActor, "actor_id"),
                (Category, DimCategory, "category_id"),
                (Store, DimStore, "store_id"),
                (Customer, DimCustomer, "customer_id"),
            ]

            for source_model, target_model, key in dimensions:
                counts[target_model.__tablename__] = _compare_keys(
                    source, target,
                    source_model, target_model,
                    key, key, errors,
                )

            # Validate relationships and date coverage.
            _check_bridges(source, target, errors)
            _check_dates(source, target, errors)

            # Validate transaction records in the date range.
            counts["fact_rental"] = _check_rentals(
                source, target, start, end, errors
            )
            counts["fact_payment"] = _check_payments(
                source, target, start, end, errors
            )

            # Validate uniqueness and foreign key integrity.
            _check_duplicates(target, errors)
            _check_foreign_keys(target, errors)

            if errors:
                raise ValueError(
                    "Validation failed:\n" + "\n".join(errors)
                )

            return {
                "status": "passed",
                "start_date": start,
                "end_date": end,
                "counts": counts,
            }

    finally:
        source_engine.dispose()
        target_engine.dispose()
