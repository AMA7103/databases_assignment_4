from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analytics_models import FactPayment
from src.database import get_target_engine
from src.sync import full_load, init
from src.validation import validate


def test_validate(test_db):
    """Check that correct data passes and corrupt totals/counts fail."""
    init(test=True)
    full_load(test=True)
    assert validate(test=True)["status"] == "passed"

    engine = get_target_engine(test=True)
    try:
        with Session(engine) as session:
            payment = session.scalar(select(FactPayment).order_by(FactPayment.payment_id))
            assert payment is not None
            original_amount = payment.amount
            payment.amount = original_amount + Decimal("2.00")
            session.commit()
            payment_id = payment.payment_id

        with pytest.raises(ValueError, match="payment totals differ"):
            validate(test=True)

        with Session(engine) as session:
            payment = session.scalar(
                select(FactPayment).where(FactPayment.payment_id == payment_id)
            )
            payment.amount = original_amount
            session.commit()
            session.delete(payment)
            session.commit()

        with pytest.raises(ValueError, match="fact_payment: source count"):
            validate(test=True)
    finally:
        engine.dispose()
