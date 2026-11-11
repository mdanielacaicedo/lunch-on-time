"""Pydantic schemas for menu-ms request / response validation."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator


# ---------- enums ----------

class CategoryEnum(str, Enum):
    lunch = "lunch"
    drink = "drink"
    snack = "snack"


class StatusEnum(str, Enum):
    AVAILABLE = "AVAILABLE"
    SOLD_OUT = "SOLD_OUT"


# ---------- request bodies ----------

class ProductCreate(BaseModel):
    """Body for POST /products."""

    name: Optional[str] = None
    category: Optional[CategoryEnum] = None
    price: Optional[Decimal] = None

    @field_validator("price", mode="before")
    @classmethod
    def price_must_be_positive(cls, v):
        if v is None:
            return v
        try:
            v = Decimal(str(v))
        except Exception:
            raise ValueError("El precio debe ser un número mayor a 0")
        if v <= 0:
            raise ValueError("El precio debe ser un número mayor a 0")
        return v


class StatusUpdate(BaseModel):
    """Body for PATCH /products/{id}/status."""

    status: StatusEnum


class PriceUpdate(BaseModel):
    """Body for PATCH /products/{id}/price (Sprint 2)."""

    price: Decimal

    @field_validator("price", mode="before")
    @classmethod
    def price_must_be_positive(cls, v):
        try:
            v = Decimal(str(v))
        except Exception:
            raise ValueError("El precio debe ser un número mayor a 0")
        if v <= 0:
            raise ValueError("El precio debe ser un número mayor a 0")
        return v


class StockUpdate(BaseModel):
    """Body for PUT /products/{id}/stock (Sprint 5)."""

    stock: int

    @field_validator("stock")
    @classmethod
    def stock_non_negative(cls, v):
        if v < 0:
            raise ValueError("Las unidades deben ser un número mayor o igual a 0")
        return v


# ---------- response bodies ----------

class ProductOut(BaseModel):
    """Product as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    price: float
    status: str
    stock: Optional[int] = None


class MenuItemOut(BaseModel):
    """Product as shown in the student menu."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    price: float
    status: str
    stock: Optional[int] = None


# ---------- labels ----------

CATEGORY_LABELS: dict[str, str] = {
    "lunch": "Almuerzo",
    "drink": "Bebida",
    "snack": "Snack",
}

STATUS_LABELS: dict[str, str] = {
    "AVAILABLE": "Disponible",
    "SOLD_OUT": "Agotado",
}
