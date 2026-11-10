import pytest

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from src.analytics_models import FactPayment
from src.database import get_source_engine, get_target_engine
from src.source_models import Payment
from src.sync import init, full_load
from src.validation import validate


def test_validate(test_db):
    """Verify validation passes and detects inconsistent data."""
    init(test=True)
    full_load(test=True)

    # A correctly synchronized database should pass validation.
    result = validate(test=True)
    assert result["status"] == "passed"

    source_engine = get_source_engine(test=True)
    target_engine = get_target_engine(test=True)

    try:
        with Session(source_engine) as source:
            # Select a payment in the default validation period.
            latest_payment = source.scalar(
                select(func.max(Payment.payment_date))
            )

            payment_id = source.scalar(
                select(Payment.payment_id)
                .where(Payment.payment_date == latest_payment)
                .limit(1)
            )

        # Deliberately remove that payment from the SQLite test database.
        with Session(target_engine) as target:
            payment = target.scalar(
                select(FactPayment).where(
                    FactPayment.payment_id == payment_id
                )
            )

            assert payment is not None

            target.delete(payment)
            target.commit()

        # Validation must detect the missing payment.
        with pytest.raises(ValueError, match="fact_payment"):
            validate(test=True)

    finally:
        source_engine.dispose()
        target_engine.dispose()
