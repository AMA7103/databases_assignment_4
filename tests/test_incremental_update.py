from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analytics_models import DimCustomer, FactRental
from src.database import get_target_engine
from src.source_models import Address, City, Customer, Rental
from src.sync import init, full_load, incremental


def test_incremental_update(source_editor):
    """Verify existing records and related changes are synchronized."""
    init(test=True)
    full_load(test=True)

    source = source_editor.session

    # Find a rental that has not been returned.
    rental = source.scalar(
        select(Rental)
        .where(Rental.return_date.is_(None))
        .order_by(Rental.rental_id)
        .limit(1)
    )
    assert rental is not None

    customer = source.get(Customer, rental.customer_id)
    address = source.get(Address, customer.address_id)

    # Choose a different city for the customer's address.
    new_city = source.scalar(
        select(City)
        .where(City.city_id != address.city_id)
        .order_by(City.city_id)
        .limit(1)
    )
    assert new_city is not None

    # Save the existing SQLite records for comparison.
    engine = get_target_engine(test=True)

    try:
        with Session(engine) as target:
            original_customer = target.scalar(
                select(DimCustomer).where(
                    DimCustomer.customer_id == customer.customer_id
                )
            )
            original_rental = target.scalar(
                select(FactRental).where(
                    FactRental.rental_id == rental.rental_id
                )
            )

            assert original_customer is not None
            assert original_rental is not None
            assert original_rental.date_key_returned is None

            customer_key = original_customer.customer_key
            rental_key = original_rental.fact_rental_key
            original_city = original_customer.city

        # Update the source records.
        timestamp = datetime.now().replace(microsecond=0) + timedelta(
            minutes=1
        )
        return_date = rental.rental_date + timedelta(days=3)

        source_editor.update(
            customer,
            first_name="UPDATED",
            last_update=timestamp,
        )

        source_editor.update(
            address,
            city_id=new_city.city_id,
            last_update=timestamp,
        )

        source_editor.update(
            rental,
            return_date=return_date,
            last_update=timestamp,
        )

        source_editor.commit()

        # Incrementally synchronize the modified records.
        incremental(test=True)

        with Session(engine) as target:
            updated_customer = target.scalar(
                select(DimCustomer).where(
                    DimCustomer.customer_id == customer.customer_id
                )
            )

            updated_rental = target.scalar(
                select(FactRental).where(
                    FactRental.rental_id == rental.rental_id
                )
            )

            # Customer attributes should be updated in place.
            assert updated_customer.first_name == "UPDATED"
            assert updated_customer.city == new_city.city
            assert updated_customer.country == new_city.country_ref.country
            assert updated_customer.city != original_city
            assert updated_customer.customer_key == customer_key

            # The returned rental should have a date and duration.
            assert updated_rental.date_key_returned == int(
                return_date.strftime("%Y%m%d")
            )
            assert updated_rental.rental_duration_days == 3
            assert updated_rental.fact_rental_key == rental_key

        # Rerunning incremental must not create duplicates.
        incremental(test=True)

        with Session(engine) as target:
            customer_count = target.scalar(
                select(func.count())
                .select_from(DimCustomer)
                .where(DimCustomer.customer_id == customer.customer_id)
            )

            rental_count = target.scalar(
                select(func.count())
                .select_from(FactRental)
                .where(FactRental.rental_id == rental.rental_id)
            )

            assert customer_count == 1
            assert rental_count == 1

            assert target.scalar(
                select(DimCustomer.customer_key).where(
                    DimCustomer.customer_id == customer.customer_id
                )
            ) == customer_key

            assert target.scalar(
                select(FactRental.fact_rental_key).where(
                    FactRental.rental_id == rental.rental_id
                )
            ) == rental_key

    finally:
        engine.dispose()
