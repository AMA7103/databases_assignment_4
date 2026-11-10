from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AnalyticsBase(DeclarativeBase):
    pass


# Dimension tables

class DimDate(AnalyticsBase):
    __tablename__ = "dim_date"

    date_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date] = mapped_column(Date, unique=True)
    year: Mapped[int] = mapped_column(Integer)
    quarter: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    day_of_month: Mapped[int] = mapped_column(Integer)
    day_of_week: Mapped[int] = mapped_column(Integer)
    is_weekend: Mapped[int] = mapped_column(Integer)


class DimFilm(AnalyticsBase):
    __tablename__ = "dim_film"

    film_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    film_id: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(String(255))
    rating: Mapped[str | None] = mapped_column(String(5))
    length: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(20))
    release_year: Mapped[int | None] = mapped_column(Integer)
    last_update: Mapped[datetime] = mapped_column(DateTime)


class DimActor(AnalyticsBase):
    __tablename__ = "dim_actor"

    actor_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(Integer, unique=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class DimCategory(AnalyticsBase):
    __tablename__ = "dim_category"

    category_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(25))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class DimStore(AnalyticsBase):
    __tablename__ = "dim_store"

    store_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    store_id: Mapped[int] = mapped_column(Integer, unique=True)
    city: Mapped[str] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class DimCustomer(AnalyticsBase):
    __tablename__ = "dim_customer"

    customer_key: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(Integer, unique=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    active: Mapped[int] = mapped_column(Integer)
    city: Mapped[str] = mapped_column(String(50))
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime] = mapped_column(DateTime)


# Bridge tables

class BridgeFilmActor(AnalyticsBase):
    __tablename__ = "bridge_film_actor"

    film_key: Mapped[int] = mapped_column(
        ForeignKey("dim_film.film_key"), primary_key=True
    )
    actor_key: Mapped[int] = mapped_column(
        ForeignKey("dim_actor.actor_key"), primary_key=True, index=True
    )


class BridgeFilmCategory(AnalyticsBase):
    __tablename__ = "bridge_film_category"

    film_key: Mapped[int] = mapped_column(
        ForeignKey("dim_film.film_key"), primary_key=True
    )
    category_key: Mapped[int] = mapped_column(
        ForeignKey("dim_category.category_key"), primary_key=True, index=True
    )


# Fact tables

class FactRental(AnalyticsBase):
    __tablename__ = "fact_rental"

    fact_rental_key: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    rental_id: Mapped[int] = mapped_column(Integer, unique=True)
    date_key_rented: Mapped[int] = mapped_column(
        ForeignKey("dim_date.date_key"), index=True
    )
    date_key_returned: Mapped[int | None] = mapped_column(
        ForeignKey("dim_date.date_key"), index=True
    )
    film_key: Mapped[int] = mapped_column(
        ForeignKey("dim_film.film_key"), index=True
    )
    store_key: Mapped[int] = mapped_column(
        ForeignKey("dim_store.store_key"), index=True
    )
    customer_key: Mapped[int] = mapped_column(
        ForeignKey("dim_customer.customer_key"), index=True
    )
    staff_id: Mapped[int] = mapped_column(Integer)
    rental_duration_days: Mapped[int | None] = mapped_column(Integer)


class FactPayment(AnalyticsBase):
    __tablename__ = "fact_payment"

    fact_payment_key: Mapped[int] = mapped_column(
        Integer, primary_key=True
    )
    payment_id: Mapped[int] = mapped_column(Integer, unique=True)
    date_key_paid: Mapped[int] = mapped_column(
        ForeignKey("dim_date.date_key"), index=True
    )
    customer_key: Mapped[int] = mapped_column(
        ForeignKey("dim_customer.customer_key"), index=True
    )
    store_key: Mapped[int] = mapped_column(
        ForeignKey("dim_store.store_key"), index=True
    )
    staff_id: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))


# Synchronization state

class SyncState(AnalyticsBase):
    __tablename__ = "sync_state"

    table_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    last_update: Mapped[datetime] = mapped_column(DateTime)
