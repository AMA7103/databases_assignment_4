from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class SourceBase(DeclarativeBase):
    pass


# Film-related tables

class Language(SourceBase):
    __tablename__ = "language"

    language_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(20))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class Film(SourceBase):
    __tablename__ = "film"

    film_id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    release_year: Mapped[int | None] = mapped_column(Integer)
    language_id: Mapped[int] = mapped_column(
        ForeignKey("language.language_id")
    )
    length: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[str | None] = mapped_column(String(5))
    last_update: Mapped[datetime] = mapped_column(DateTime)

    language: Mapped["Language"] = relationship()


class Actor(SourceBase):
    __tablename__ = "actor"

    actor_id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class Category(SourceBase):
    __tablename__ = "category"

    category_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(25))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class FilmActor(SourceBase):
    __tablename__ = "film_actor"

    actor_id: Mapped[int] = mapped_column(
        ForeignKey("actor.actor_id"), primary_key=True
    )
    film_id: Mapped[int] = mapped_column(
        ForeignKey("film.film_id"), primary_key=True
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    actor: Mapped["Actor"] = relationship()
    film: Mapped["Film"] = relationship()


class FilmCategory(SourceBase):
    __tablename__ = "film_category"

    film_id: Mapped[int] = mapped_column(
        ForeignKey("film.film_id"), primary_key=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("category.category_id"), primary_key=True
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    film: Mapped["Film"] = relationship()
    category: Mapped["Category"] = relationship()


# Location-related tables

class Country(SourceBase):
    __tablename__ = "country"

    country_id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(50))
    last_update: Mapped[datetime] = mapped_column(DateTime)


class City(SourceBase):
    __tablename__ = "city"

    city_id: Mapped[int] = mapped_column(primary_key=True)
    city: Mapped[str] = mapped_column(String(50))
    country_id: Mapped[int] = mapped_column(
        ForeignKey("country.country_id")
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    country_ref: Mapped["Country"] = relationship()


class Address(SourceBase):
    __tablename__ = "address"

    address_id: Mapped[int] = mapped_column(primary_key=True)
    city_id: Mapped[int] = mapped_column(
        ForeignKey("city.city_id")
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    city_ref: Mapped["City"] = relationship()


# Store and customer tables

class Store(SourceBase):
    __tablename__ = "store"

    store_id: Mapped[int] = mapped_column(primary_key=True)
    manager_staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.staff_id")
    )
    address_id: Mapped[int] = mapped_column(
        ForeignKey("address.address_id")
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    address: Mapped["Address"] = relationship()


class Staff(SourceBase):
    __tablename__ = "staff"

    staff_id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("store.store_id")
    )
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    last_update: Mapped[datetime] = mapped_column(DateTime)

    store: Mapped["Store"] = relationship(
        foreign_keys=[store_id]
    )


class Customer(SourceBase):
    __tablename__ = "customer"

    customer_id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(
        ForeignKey("store.store_id")
    )
    first_name: Mapped[str] = mapped_column(String(45))
    last_name: Mapped[str] = mapped_column(String(45))
    address_id: Mapped[int] = mapped_column(
        ForeignKey("address.address_id")
    )
    active: Mapped[int] = mapped_column(Integer)
    last_update: Mapped[datetime] = mapped_column(DateTime)

    store: Mapped["Store"] = relationship()
    address: Mapped["Address"] = relationship()


# Inventory, rental, and payment tables

class Inventory(SourceBase):
    __tablename__ = "inventory"

    inventory_id: Mapped[int] = mapped_column(primary_key=True)
    film_id: Mapped[int] = mapped_column(
        ForeignKey("film.film_id")
    )
    store_id: Mapped[int] = mapped_column(
        ForeignKey("store.store_id")
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    film: Mapped["Film"] = relationship()
    store: Mapped["Store"] = relationship()


class Rental(SourceBase):
    __tablename__ = "rental"

    rental_id: Mapped[int] = mapped_column(primary_key=True)
    rental_date: Mapped[datetime] = mapped_column(DateTime)
    inventory_id: Mapped[int] = mapped_column(
        ForeignKey("inventory.inventory_id")
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.customer_id")
    )
    return_date: Mapped[datetime | None] = mapped_column(DateTime)
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.staff_id")
    )
    last_update: Mapped[datetime] = mapped_column(DateTime)

    inventory: Mapped["Inventory"] = relationship()
    customer: Mapped["Customer"] = relationship()
    staff: Mapped["Staff"] = relationship()


class Payment(SourceBase):
    __tablename__ = "payment"

    payment_id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.customer_id")
    )
    staff_id: Mapped[int] = mapped_column(
        ForeignKey("staff.staff_id")
    )
    rental_id: Mapped[int | None] = mapped_column(
        ForeignKey("rental.rental_id")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    payment_date: Mapped[datetime] = mapped_column(DateTime)
    last_update: Mapped[datetime] = mapped_column(DateTime)

    customer: Mapped["Customer"] = relationship()
    staff: Mapped["Staff"] = relationship()
    rental: Mapped["Rental | None"] = relationship()
