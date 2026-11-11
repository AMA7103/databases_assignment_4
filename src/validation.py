from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import (
    BridgeFilmActor, BridgeFilmCategory, DimActor, DimCategory,
    DimCustomer, DimDate, DimFilm, DimStore, FactPayment, FactRental,
)
from src.database import get_source_engine, get_target_engine
from src.source_models import (
    Actor, Category, Customer, Film, FilmActor, FilmCategory,
    Inventory, Payment, Rental, Staff, Store,
)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _date_key(value):
    return int(value.strftime("%Y%m%d"))


def _compare_keys(source, target, source_model, target_model, key, errors):
    source_values = list(source.scalars(select(getattr(source_model, key))))
    target_values = list(target.scalars(select(getattr(target_model, key))))
    name = target_model.__tablename__
    if len(source_values) != len(target_values):
        errors.append(f"{name}: source count {len(source_values)}, target count {len(target_values)}")
    missing = set(source_values) - set(target_values)
    extra = set(target_values) - set(source_values)
    if missing or extra:
        errors.append(f"{name}: {len(missing)} missing, {len(extra)} unexpected natural keys")
    return len(source_values)


def _check_bridges(source, target, errors):
    comparisons = [
        (
            "bridge_film_actor",
            set(source.execute(select(FilmActor.film_id, FilmActor.actor_id))),
            set(target.execute(
                select(DimFilm.film_id, DimActor.actor_id)
                .select_from(BridgeFilmActor)
                .join(DimFilm, BridgeFilmActor.film_key == DimFilm.film_key)
                .join(DimActor, BridgeFilmActor.actor_key == DimActor.actor_key)
            )),
            source.scalar(select(func.count()).select_from(FilmActor)),
            target.scalar(select(func.count()).select_from(BridgeFilmActor)),
        ),
        (
            "bridge_film_category",
            set(source.execute(select(FilmCategory.film_id, FilmCategory.category_id))),
            set(target.execute(
                select(DimFilm.film_id, DimCategory.category_id)
                .select_from(BridgeFilmCategory)
                .join(DimFilm, BridgeFilmCategory.film_key == DimFilm.film_key)
                .join(DimCategory, BridgeFilmCategory.category_key == DimCategory.category_key)
            )),
            source.scalar(select(func.count()).select_from(FilmCategory)),
            target.scalar(select(func.count()).select_from(BridgeFilmCategory)),
        ),
    ]
    for name, original, copied, source_count, target_count in comparisons:
        if source_count != target_count:
            errors.append(f"{name}: source count {source_count}, target count {target_count}")
        if original != copied:
            errors.append(f"{name}: {len(original - copied)} missing, {len(copied - original)} unexpected pairs")


def _check_dates(source, target, errors):
    needed = set()
    for column in (Rental.rental_date, Rental.return_date, Payment.payment_date):
        values = source.scalars(select(func.date(column)).where(column.is_not(None)).distinct())
        needed.update(_date_key(_as_date(value)) for value in values)
    existing = set(target.scalars(select(DimDate.date_key)))
    if needed - existing:
        errors.append(f"dim_date: {len(needed - existing)} missing dates")


def _check_duplicates(target, errors):
    keys = [
        (DimFilm, DimFilm.film_id), (DimActor, DimActor.actor_id),
        (DimCategory, DimCategory.category_id), (DimStore, DimStore.store_id),
        (DimCustomer, DimCustomer.customer_id), (DimDate, DimDate.date_key),
        (FactRental, FactRental.rental_id), (FactPayment, FactPayment.payment_id),
    ]
    for model, column in keys:
        duplicate = target.execute(
            select(column).group_by(column).having(func.count() > 1).limit(1)
        ).first()
        if duplicate:
            errors.append(f"{model.__tablename__}: duplicate natural key")
    for model in (BridgeFilmActor, BridgeFilmCategory):
        columns = list(model.__table__.primary_key.columns)
        duplicate = target.execute(
            select(*columns).group_by(*columns).having(func.count() > 1).limit(1)
        ).first()
        if duplicate:
            errors.append(f"{model.__tablename__}: duplicate relationship")


def _check_foreign_keys(target, errors):
    for model in (BridgeFilmActor, BridgeFilmCategory, FactRental, FactPayment):
        table = model.__table__
        for fk in table.foreign_keys:
            parent = fk.parent
            referenced = fk.column
            invalid = target.scalar(
                select(func.count())
                .select_from(table.outerjoin(referenced.table, parent == referenced))
                .where(parent.is_not(None), referenced.is_(None))
            )
            if invalid:
                errors.append(f"{table.name}.{parent.name}: {invalid} broken references")


def _check_facts(source, target, start, end, errors):
    """Compare all records and payment totals; also check the selected period."""
    source_rentals = source.execute(
        select(Rental.rental_id, Rental.rental_date)
    ).all()
    target_rentals = target.execute(
        select(FactRental.rental_id, FactRental.date_key_rented)
    ).all()

    source_payments = source.execute(
        select(Payment.payment_id, Payment.amount, Payment.payment_date,
               func.coalesce(Inventory.store_id, Staff.store_id))
        .select_from(Payment)
        .outerjoin(Rental, Payment.rental_id == Rental.rental_id)
        .outerjoin(Inventory, Rental.inventory_id == Inventory.inventory_id)
        .join(Staff, Payment.staff_id == Staff.staff_id)
    ).all()
    target_payments = target.execute(
        select(FactPayment.payment_id, FactPayment.amount,
               FactPayment.date_key_paid, DimStore.store_id)
        .select_from(FactPayment)
        .outerjoin(DimStore, FactPayment.store_key == DimStore.store_key)
    ).all()

    start_key, end_key = _date_key(start), _date_key(end)
    source_rental_ids = {row[0] for row in source_rentals}
    target_rental_ids = {row[0] for row in target_rentals}
    source_payment_ids = {row[0] for row in source_payments}
    target_payment_ids = {row[0] for row in target_payments}

    for name, original, copied, source_count, target_count in (
        ("fact_rental", source_rental_ids, target_rental_ids,
         len(source_rentals), len(target_rentals)),
        ("fact_payment", source_payment_ids, target_payment_ids,
         len(source_payments), len(target_payments)),
    ):
        if source_count != target_count:
            errors.append(f"{name}: source count {source_count}, target count {target_count}")
        if original != copied:
            errors.append(f"{name}: {len(original - copied)} missing, {len(copied - original)} unexpected IDs")

    rentals_in_period = {
        row[0] for row in source_rentals if start <= row[1].date() <= end
    }
    copied_rentals_in_period = {
        row[0] for row in target_rentals if start_key <= row[1] <= end_key
    }
    if rentals_in_period != copied_rentals_in_period:
        errors.append("fact_rental: selected date range has mismatched rental IDs")

    payments_in_period = {
        row[0] for row in source_payments if start <= row[2].date() <= end
    }
    copied_payments_in_period = {
        row[0] for row in target_payments if start_key <= row[2] <= end_key
    }
    if payments_in_period != copied_payments_in_period:
        errors.append("fact_payment: selected date range has mismatched payment IDs")

    for label, scoped in (("all dates", False), ("selected date range", True)):
        source_totals = defaultdict(Decimal)
        target_totals = defaultdict(Decimal)
        for _, amount, paid_at, store_id in source_payments:
            if not scoped or start <= paid_at.date() <= end:
                source_totals[store_id] += amount
        for _, amount, key, store_id in target_payments:
            if not scoped or start_key <= key <= end_key:
                target_totals[store_id] += amount
        for store_id in source_totals.keys() | target_totals.keys():
            if abs(source_totals[store_id] - target_totals[store_id]) > Decimal("0.01"):
                errors.append(f"Store {store_id}: payment totals differ for {label}")

    return {"fact_rental": len(rentals_in_period), "fact_payment": len(payments_in_period)}


def validate_sessions(source, target, start_date=None, end_date=None):
    """Validate using existing sessions, including uncommitted target changes."""
    target.flush()
    latest = source.scalar(select(func.max(Payment.payment_date)))
    if latest is None:
        raise ValueError("No payments found in Sakila")
    end = _as_date(end_date) if end_date else latest.date()
    start = _as_date(start_date) if start_date else end - timedelta(days=29)
    if start > end:
        raise ValueError("Start date cannot be after end date")

    errors = []
    counts = {}
    for source_model, target_model, key in (
        (Film, DimFilm, "film_id"), (Actor, DimActor, "actor_id"),
        (Category, DimCategory, "category_id"), (Store, DimStore, "store_id"),
        (Customer, DimCustomer, "customer_id"),
    ):
        counts[target_model.__tablename__] = _compare_keys(
            source, target, source_model, target_model, key, errors
        )
    _check_bridges(source, target, errors)
    _check_dates(source, target, errors)
    _check_duplicates(target, errors)
    _check_foreign_keys(target, errors)
    counts.update(_check_facts(source, target, start, end, errors))

    if errors:
        raise ValueError("Validation failed:\n" + "\n".join(errors))
    return {"status": "passed", "start_date": start, "end_date": end, "counts": counts}


def validate(start_date=None, end_date=None, test=False):
    """Run standalone validation against the requested databases."""
    source_engine = get_source_engine(test)
    target_engine = get_target_engine(test)
    try:
        with Session(source_engine) as source, Session(target_engine) as target:
            return validate_sessions(source, target, start_date, end_date)
    finally:
        source_engine.dispose()
        target_engine.dispose()
