"""SQLAlchemy model for the products table (menu-ms)."""

from sqlalchemy import (
    Column,
    Integer,
    Numeric,
    Text,
    CheckConstraint,
    Index,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Product(Base):
    """Represents a cafeteria product.

    Fields follow section 7.1 of the design document.
    """

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    price = Column(Numeric, nullable=False)
    status = Column(Text, nullable=False, server_default="AVAILABLE")
    stock = Column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "category IN ('lunch', 'drink', 'snack')",
            name="ck_products_category",
        ),
        CheckConstraint("price > 0", name="ck_products_price_positive"),
        CheckConstraint(
            "status IN ('AVAILABLE', 'SOLD_OUT')",
            name="ck_products_status",
        ),
        CheckConstraint(
            "stock IS NULL OR stock >= 0",
            name="ck_products_stock_non_negative",
        ),
        # Case-insensitive unique name
        Index("uq_products_name_lower", "name", unique=True),
    )
