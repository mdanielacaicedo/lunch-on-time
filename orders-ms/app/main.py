"""orders-ms · FastAPI application.

Endpoints for advance orders management.
Sprint 2 implements:
- POST /orders (CF-PEDI-0001, CF-PEDI-0002)
- GET /health
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

try:
    from zoneinfo import ZoneInfo
    BOGOTA_TZ = ZoneInfo("America/Bogota")
except Exception:
    BOGOTA_TZ = timezone(timedelta(hours=-5))


def get_now_bogota() -> datetime:
    try:
        return datetime.now(BOGOTA_TZ)
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5)))

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, init_db
from app.menu_client import (
    MenuClientError,
    MenuUnavailableError,
    ProductSoldOutError,
    release_products,
    reserve_products,
)
from app.models import Order, OrderItem
from app.schemas import OrderCreate, OrderItemOut, OrderOut

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="orders-ms",
    description="Microservicio de pedidos — Almuerzo a Tiempo",
    lifespan=lifespan,
)


# ---------- exception handlers ----------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 400 with acceptance-criteria messages instead of 422."""
    for error in exc.errors():
        msg = error.get("msg", "")
        if "La cantidad debe ser mayor a 0" in msg:
            return JSONResponse(status_code=400, content={"detail": msg})
    return JSONResponse(
        status_code=400,
        content={"detail": "Complete los campos obligatorios"},
    )


@app.exception_handler(MenuUnavailableError)
async def menu_unavailable_handler(request: Request, exc: MenuUnavailableError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(ProductSoldOutError)
async def product_sold_out_handler(request: Request, exc: ProductSoldOutError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(MenuClientError)
async def menu_client_error_handler(request: Request, exc: MenuClientError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------- GET /health ----------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- POST /orders (CF-PEDI-0001, CF-PEDI-0002) ----------

@app.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(body: OrderCreate, db: AsyncSession = Depends(get_db)):
    # 1. Validate empty order (CF-PEDI-0001 esc 2)
    if not body.items or len(body.items) == 0:
        raise HTTPException(status_code=400, detail="Agrega al menos un producto")

    # 2. Validate mandatory customer info and slot
    if (
        not body.customer_name
        or not body.customer_name.strip()
        or not body.student_code
        or not body.student_code.strip()
        or not body.slot
        or not body.slot.strip()
    ):
        raise HTTPException(status_code=400, detail="Complete los campos obligatorios")

    raw_items = [
        {"product_id": item.product_id, "quantity": item.quantity}
        for item in body.items
    ]

    # 3. Reserve products in menu-ms (timeout 3s, checks sold_out & stock)
    # May raise MenuUnavailableError (503) or ProductSoldOutError (409)
    reserved_items = await reserve_products(raw_items)

    # 4. Determine order date in America/Bogota (avoids UTC date rollover at 7 PM)
    now_bogota = get_now_bogota()
    order_date = now_bogota.date()
    lock_key = int(order_date.strftime("%Y%m%d"))

    # 5. Database transaction with advisory transaction lock per date
    try:
        # Transaction-level advisory lock using date as key
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

        # Get daily sequential number
        num_stmt = select(func.coalesce(func.max(Order.number), 0) + 1).where(
            Order.date == order_date
        )
        res = await db.execute(num_stmt)
        next_number = res.scalar_one()

        # Calculate total
        total = Decimal("0.0")
        for item in reserved_items:
            unit_price = Decimal(str(item["unit_price"]))
            total += unit_price * item["quantity"]

        # Create Order record
        order = Order(
            date=order_date,
            number=next_number,
            customer_name=body.customer_name.strip(),
            student_code=body.student_code.strip(),
            slot=body.slot.strip(),
            status="RECEIVED",
            total=total,
        )
        db.add(order)
        await db.flush()  # assign order.id

        # Create OrderItem records
        for item in reserved_items:
            order_item = OrderItem(
                order_id=order.id,
                product_id=item["product_id"],
                product_name=item["product_name"],
                unit_price=Decimal(str(item["unit_price"])),
                quantity=item["quantity"],
            )
            db.add(order_item)

        await db.commit()

    except Exception as exc:
        # DB failure after reservation -> SAGA COMPENSATION: release reserved units
        logger.error(f"Failed to persist order, executing compensation release: {exc}")
        await db.rollback()
        await release_products(raw_items)
        raise HTTPException(
            status_code=500,
            detail="Error interno al registrar el pedido",
        ) from exc

    items_out = [
        OrderItemOut(
            product_id=item["product_id"],
            product_name=item["product_name"],
            unit_price=float(item["unit_price"]),
            quantity=item["quantity"],
        )
        for item in reserved_items
    ]

    return OrderOut(
        number=f"{next_number:03d}",
        status=order.status,
        slot=order.slot,
        total=float(order.total),
        customer_name=order.customer_name,
        student_code=order.student_code,
        items=items_out,
    )
