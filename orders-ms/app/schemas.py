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


class SlotOut(BaseModel):
    """15-minute pickup slot."""

    slot: str
    status: str  # available, past, full


class OrderListOut(BaseModel):
    """Orders list response matching CF-PEDI-0004."""

    orders: List[OrderOut]
    message: Optional[str] = None


class ConfigOut(BaseModel):
    """System configuration response matching section 8.2."""

    model_config = ConfigDict(from_attributes=True)

    slot_capacity: int
    start_time: str
    end_time: str


class ConfigUpdate(BaseModel):
    """Payload for PUT /config."""

    slot_capacity: Optional[int] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @field_validator("slot_capacity", mode="before")
    @classmethod
    def capacity_must_be_positive(cls, v):
        if v is None:
            return v
        if isinstance(v, bool):
            raise ValueError("El cupo debe ser un número entero mayor a 0")
        try:
            if isinstance(v, float) and not v.is_integer():
                raise ValueError("El cupo debe ser un número entero mayor a 0")
            v_int = int(v)
        except (ValueError, TypeError):
            raise ValueError("El cupo debe ser un número entero mayor a 0")
        if v_int <= 0:
            raise ValueError("El cupo debe ser un número entero mayor a 0")
        return v_int


class OrderStatusOut(BaseModel):
    """Order status response for student query (CF-PEDI-0006)."""

    model_config = ConfigDict(from_attributes=True)

    number: str
    status: str
    slot: str
    message: Optional[str] = None


class OrderDeliver(BaseModel):
    """Payload for POST /orders/{number}/deliver (CF-PEDI-0007)."""

    payment_method: Optional[str] = None

    @field_validator("payment_method")
    @classmethod
    def validate_payment_method(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip().upper()
            if v not in ("CASH", "TRANSFER"):
                raise ValueError("Selecciona el medio de pago")
        return v


class OrderCancel(BaseModel):
    """Payload for POST /orders/{number}/cancel (CF-PEDI-0008)."""

    student_code: Optional[str] = None


class SalesTodayOut(BaseModel):
    """Sales report for today matching CF-PEDI-0007."""

    date: str
    cash: float
    transfer: float
    total: float
    count: int



