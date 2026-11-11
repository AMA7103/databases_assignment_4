from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import DimCustomer, FactRental
from src.database import get_target_engine
from src.source_models import Address, City, Customer, Rental
from src.sync import full_load, incremental, init


def test_incremental_update(source_editor):
    """Check rental returns, customer edits, and independent address changes."""
    init(test=True)
    full_load(test=True)
    source = source_editor.session

    rental = source.scalar(
        select(Rental).where(Rental.return_date.is_(None))
        .order_by(Rental.rental_id).limit(1)
    )
    assert rental is not None
    customer = source.get(Customer, rental.customer_id)
    address = source.get(Address, customer.address_id)
    other_city = source.scalar(
        select(City).where(City.city_id != address.city_id)
        .order_by(City.city_id).limit(1)
    )
    assert other_city is not None

    engine = get_target_engine(test=True)
    try:
        with Session(engine) as target:
            original_customer = target.scalar(
                select(DimCustomer).where(DimCustomer.customer_id == customer.customer_id)
            )
            original_rental = target.scalar(
                select(FactRental).where(FactRental.rental_id == rental.rental_id)
            )
            assert original_customer is not None and original_rental is not None
            assert original_rental.date_key_returned is None
            customer_key = original_customer.customer_key
            rental_key = original_rental.fact_rental_key
            old_city = original_customer.city

        timestamp = datetime.now().replace(microsecond=0) + timedelta(minutes=1)
        return_date = rental.rental_date + timedelta(days=3)
        source_editor.update(customer, first_name="UPDATED", last_update=timestamp)
        source_editor.update(rental, return_date=return_date, last_update=timestamp)
        source_editor.commit()
        incremental(test=True)

        with Session(engine) as target:
            copied_customer = target.scalar(
                select(DimCustomer).where(DimCustomer.customer_id == customer.customer_id)
            )
            copied_rental = target.scalar(
                select(FactRental).where(FactRental.rental_id == rental.rental_id)
            )
            assert copied_customer.first_name == "UPDATED"
            assert copied_customer.city == old_city
            assert copied_customer.customer_key == customer_key
            assert copied_rental.date_key_returned == int(return_date.strftime("%Y%m%d"))
            assert copied_rental.rental_duration_days == 3
            assert copied_rental.fact_rental_key == rental_key

        # Update only address; customer.last_update is intentionally unchanged.
        source_editor.update(
            address, city_id=other_city.city_id,
            last_update=timestamp + timedelta(minutes=1),
        )
        source_editor.commit()
        incremental(test=True)
        incremental(test=True)  # Boundary timestamps must be safe to reread.

        with Session(engine) as target:
            copied_customer = target.scalar(
                select(DimCustomer).where(DimCustomer.customer_id == customer.customer_id)
            )
            assert copied_customer.city == other_city.city
            assert copied_customer.country == other_city.country_ref.country
            assert copied_customer.city != old_city
            assert copied_customer.customer_key == customer_key
            assert target.scalar(
                select(func.count()).select_from(DimCustomer)
                .where(DimCustomer.customer_id == customer.customer_id)
            ) == 1
            assert target.scalar(
                select(func.count()).select_from(FactRental)
                .where(FactRental.rental_id == rental.rental_id)
            ) == 1
    finally:
        engine.dispose()
