"""Pydantic schemas for orders-ms."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, field_validator


class StatusEnum(str, Enum):
    RECEIVED = "RECEIVED"
    READY = "READY"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class PaymentMethodEnum(str, Enum):
    CASH = "CASH"
    TRANSFER = "TRANSFER"


class OrderItemCreate(BaseModel):
    """Product item inside order creation request."""

    product_id: int
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("La cantidad debe ser mayor a 0")
        return v


class OrderCreate(BaseModel):
    """Payload for POST /orders."""

    customer_name: Optional[str] = None
    student_code: Optional[str] = None
    slot: Optional[str] = None
    items: Optional[List[OrderItemCreate]] = None


class OrderItemOut(BaseModel):
    """Snapshot item in responses."""

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    product_name: str
    unit_price: float
    quantity: int


class OrderOut(BaseModel):
    """Order response matching section 8.3."""

    model_config = ConfigDict(from_attributes=True)

    number: str
    status: str
    slot: str
    total: float
    customer_name: Optional[str] = None
    student_code: Optional[str] = None
    items: Optional[List[OrderItemOut]] = None
