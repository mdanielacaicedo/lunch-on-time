"""SQLAlchemy models for orders-ms.

Follows section 7.2 of docs/design.md:
- orders
- order_items
- config
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Order(Base):
    """Represents a student's advance order."""

    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    number = Column(Integer, nullable=False)
    customer_name = Column(Text, nullable=False)
    student_code = Column(Text, nullable=False)
    slot = Column(Text, nullable=False)  # HH:MM format
    status = Column(Text, nullable=False, server_default="RECEIVED")
    payment_method = Column(Text, nullable=True)
    total = Column(Numeric, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("date", "number", name="uq_orders_date_number"),
        CheckConstraint(
            "status IN ('RECEIVED', 'READY', 'DELIVERED', 'CANCELLED')",
            name="ck_orders_status",
        ),
        CheckConstraint(
            "payment_method IS NULL OR payment_method IN ('CASH', 'TRANSFER')",
            name="ck_orders_payment_method",
        ),
    )


class OrderItem(Base):
    """Snapshot of a product item within an order."""

    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, nullable=False)
    product_name = Column(Text, nullable=False)
    unit_price = Column(Numeric, nullable=False)
    quantity = Column(Integer, nullable=False)

    order = relationship("Order", back_populates="items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
    )


class Config(Base):
    """System configuration parameters."""

    __tablename__ = "config"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)
