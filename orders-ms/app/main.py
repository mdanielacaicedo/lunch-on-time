"""orders-ms · FastAPI application.

Endpoints for advance orders management.
Sprint 2 implements:
- POST /orders (CF-PEDI-0001, CF-PEDI-0002)
- GET /health
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

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

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db import get_db, init_db
from app.menu_client import (
    MenuClientError,
    MenuUnavailableError,
    ProductSoldOutError,
    release_products,
    reserve_products,
)
from app.models import Config, Order, OrderItem
from app.schemas import (
    ConfigOut,
    ConfigUpdate,
    OrderCancel,
    OrderCreate,
    OrderDeliver,
    OrderItemOut,
    OrderListOut,
    OrderOut,
    OrderStatusOut,
    SalesTodayOut,
    SlotOut,
)

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
        if "El cupo debe ser un número entero mayor a 0" in msg:
            return JSONResponse(status_code=400, content={"detail": msg})
        if "Selecciona el medio de pago" in msg:
            return JSONResponse(status_code=400, content={"detail": "Selecciona el medio de pago"})
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


# ---------- System config & slot helpers ----------

async def get_system_config(db: AsyncSession) -> dict[str, str]:
    """Retrieve system configuration values with fallback defaults."""
    result = await db.execute(select(Config))
    rows = result.scalars().all()
    configs = {row.key: row.value for row in rows}
    defaults = {
        "slot_capacity": "10",
        "start_time": "11:30",
        "end_time": "14:00",
    }
    for k, v in defaults.items():
        if k not in configs:
            configs[k] = v
    return configs


def get_generated_slot_times(start_str: str, end_str: str) -> list[str]:
    """Generate 15-minute slot times between start_time and end_time (exclusive of end_time)."""
    start_t = datetime.strptime(start_str, "%H:%M").time()
    end_t = datetime.strptime(end_str, "%H:%M").time()
    dummy_date = datetime(2000, 1, 1)
    curr_dt = datetime.combine(dummy_date, start_t)
    end_dt = datetime.combine(dummy_date, end_t)

    slots: list[str] = []
    while curr_dt < end_dt:
        slots.append(curr_dt.strftime("%H:%M"))
        curr_dt += timedelta(minutes=15)
    return slots


# ---------- GET /health ----------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- GET /slots (CF-PEDI-0003) ----------

@app.get("/slots", response_model=List[SlotOut])
async def get_slots(db: AsyncSession = Depends(get_db)):
    """Return today's 15-minute pickup slots with status: available, past, full."""
    configs = await get_system_config(db)
    start_str = configs.get("start_time", "11:30")
    end_str = configs.get("end_time", "14:00")
    capacity = int(configs.get("slot_capacity", "10"))

    valid_slots = get_generated_slot_times(start_str, end_str)

    now_bogota = get_now_bogota()
    today = now_bogota.date()
    cutoff = now_bogota + timedelta(minutes=15)

    stmt = (
        select(Order.slot, func.count(Order.id))
        .where(
            Order.date == today,
            Order.status != "CANCELLED",
        )
        .group_by(Order.slot)
    )
    res = await db.execute(stmt)
    counts = dict(res.all())

    slots_out: list[SlotOut] = []
    for slot_str in valid_slots:
        slot_t = datetime.strptime(slot_str, "%H:%M").time()
        slot_dt = datetime.combine(today, slot_t, tzinfo=now_bogota.tzinfo)

        if slot_dt < cutoff:
            status = "past"
        elif counts.get(slot_str, 0) >= capacity:
            status = "full"
        else:
            status = "available"

        slots_out.append(SlotOut(slot=slot_str, status=status))

    return slots_out


# ---------- POST /orders (CF-PEDI-0001, CF-PEDI-0002, CF-PEDI-0003) ----------

@app.post("/orders", response_model=OrderOut, status_code=201)
async def create_order(body: OrderCreate, db: AsyncSession = Depends(get_db)):
    # 1. Validate empty order (CF-PEDI-0001 esc 2)
    if not body.items or len(body.items) == 0:
        raise HTTPException(status_code=400, detail="Agrega al menos un producto")

    # 2. Validate mandatory customer info
    if (
        not body.customer_name
        or not body.customer_name.strip()
        or not body.student_code
        or not body.student_code.strip()
        or body.slot is None
    ):
        raise HTTPException(status_code=400, detail="Complete los campos obligatorios")

    # 3. Validate slot selection and schedule (CF-PEDI-0003)
    slot_str = body.slot.strip()
    if not slot_str:
        raise HTTPException(status_code=400, detail="Selecciona una hora de recogida")

    configs = await get_system_config(db)
    valid_slots = get_generated_slot_times(
        configs.get("start_time", "11:30"),
        configs.get("end_time", "14:00"),
    )
    if slot_str not in valid_slots:
        raise HTTPException(status_code=400, detail="Selecciona una hora de recogida")

    now_bogota = get_now_bogota()
    order_date = now_bogota.date()
    slot_t = datetime.strptime(slot_str, "%H:%M").time()
    slot_dt = datetime.combine(order_date, slot_t, tzinfo=now_bogota.tzinfo)
    if slot_dt < now_bogota + timedelta(minutes=15):
        raise HTTPException(status_code=400, detail="Selecciona una hora de recogida")

    raw_items = [
        {"product_id": item.product_id, "quantity": item.quantity}
        for item in body.items
    ]

    # 4. Reserve products in menu-ms (timeout 3s, checks sold_out & stock)
    # May raise MenuUnavailableError (503) or ProductSoldOutError (409)
    reserved_items = await reserve_products(raw_items)

    lock_key = int(order_date.strftime("%Y%m%d"))

    # 5. Database transaction with advisory transaction lock per date
    try:
        # Transaction-level advisory lock using date as key
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

        # Check slot capacity inside the advisory lock before inserting
        count_stmt = select(func.count(Order.id)).where(
            Order.date == order_date,
            Order.slot == slot_str,
            Order.status != "CANCELLED",
        )
        res_count = await db.execute(count_stmt)
        current_slot_orders = res_count.scalar_one()

        slot_capacity = int(configs.get("slot_capacity", "10"))
        if current_slot_orders >= slot_capacity:
            await db.rollback()
            await release_products(raw_items)
            raise HTTPException(
                status_code=409,
                detail="La franja seleccionada está llena",
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
            slot=slot_str,
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

    except HTTPException:
        raise
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


# ---------- GET /orders (CF-PEDI-0004, CF-PEDI-0005) ----------

@app.get("/orders", response_model=OrderListOut)
async def list_orders(
    status: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """List today's orders ordered by slot and number."""
    now_bogota = get_now_bogota()
    today = now_bogota.date()

    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.date == today)
    )

    if status:
        stmt = stmt.where(Order.status == status)
    else:
        stmt = stmt.where(Order.status != "CANCELLED")

    stmt = stmt.order_by(Order.slot.asc(), Order.number.asc())
    res = await db.execute(stmt)
    orders = res.scalars().all()

    orders_out = [
        OrderOut(
            number=f"{o.number:03d}",
            status=o.status,
            slot=o.slot,
            total=float(o.total),
            customer_name=o.customer_name,
            student_code=o.student_code,
            items=[
                OrderItemOut(
                    product_id=it.product_id,
                    product_name=it.product_name,
                    unit_price=float(it.unit_price),
                    quantity=it.quantity,
                )
                for it in o.items
            ],
        )
        for o in orders
    ]

    message = None
    if not orders_out and status == "RECEIVED":
        message = "No hay pedidos pendientes"

    return OrderListOut(orders=orders_out, message=message)


# ---------- POST /orders/{number}/ready (CF-PEDI-0005) ----------

@app.post("/orders/{number}/ready", response_model=OrderOut)
async def mark_order_ready(number: str, db: AsyncSession = Depends(get_db)):
    """Mark an order as READY from RECEIVED."""
    try:
        num = int(number)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    now_bogota = get_now_bogota()
    today = now_bogota.date()

    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.date == today,
            Order.number == num,
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    order = res.scalars().first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    if order.status == "CANCELLED":
        raise HTTPException(
            status_code=409,
            detail="Este pedido fue cancelado",
        )

    if order.status == "DELIVERED":
        raise HTTPException(
            status_code=409,
            detail="Este pedido ya fue entregado",
        )

    if order.status == "READY":
        # Idempotent: return without error
        return OrderOut(
            number=f"{order.number:03d}",
            status=order.status,
            slot=order.slot,
            total=float(order.total),
            customer_name=order.customer_name,
            student_code=order.student_code,
            items=[
                OrderItemOut(
                    product_id=it.product_id,
                    product_name=it.product_name,
                    unit_price=float(it.unit_price),
                    quantity=it.quantity,
                )
                for it in order.items
            ],
        )

    # Transition RECEIVED -> READY
    order.status = "READY"
    await db.commit()
    await db.refresh(order)

    return OrderOut(
        number=f"{order.number:03d}",
        status=order.status,
        slot=order.slot,
        total=float(order.total),
        customer_name=order.customer_name,
        student_code=order.student_code,
        items=[
            OrderItemOut(
                product_id=it.product_id,
                product_name=it.product_name,
                unit_price=float(it.unit_price),
                quantity=it.quantity,
            )
            for it in order.items
        ],
    )


# ---------- GET /orders/{number} (CF-PEDI-0006) ----------

@app.get("/orders/{number}", response_model=OrderStatusOut)
async def get_order_status(number: str, db: AsyncSession = Depends(get_db)):
    """Retrieve order status and pickup slot for the student (CF-PEDI-0006)."""
    try:
        num = int(number)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    now_bogota = get_now_bogota()
    today = now_bogota.date()

    stmt = select(Order).where(Order.date == today, Order.number == num)
    res = await db.execute(stmt)
    order = res.scalars().first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    message = None
    if order.status == "READY":
        message = "Tu pedido está listo, recógelo en la caja de pedidos anticipados"

    return OrderStatusOut(
        number=f"{order.number:03d}",
        status=order.status,
        slot=order.slot,
        message=message,
    )


# ---------- POST /orders/{number}/deliver (CF-PEDI-0007) ----------

@app.post("/orders/{number}/deliver", response_model=OrderOut)
async def deliver_order(
    number: str,
    body: OrderDeliver,
    db: AsyncSession = Depends(get_db),
):
    """Mark an order as DELIVERED and register payment method (CF-PEDI-0007)."""
    if not body.payment_method or body.payment_method not in ("CASH", "TRANSFER"):
        raise HTTPException(status_code=400, detail="Selecciona el medio de pago")

    try:
        num = int(number)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    now_bogota = get_now_bogota()
    today = now_bogota.date()

    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.date == today,
            Order.number == num,
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    order = res.scalars().first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    if order.status == "CANCELLED":
        raise HTTPException(
            status_code=409,
            detail="Este pedido fue cancelado",
        )

    if order.status == "DELIVERED":
        raise HTTPException(
            status_code=409,
            detail="Este pedido ya fue entregado",
        )

    if order.status == "RECEIVED":
        raise HTTPException(
            status_code=409,
            detail="Primero marca el pedido como Listo",
        )

    order.status = "DELIVERED"
    order.payment_method = body.payment_method
    order.delivered_at = now_bogota
    await db.commit()
    await db.refresh(order)

    return OrderOut(
        number=f"{order.number:03d}",
        status=order.status,
        slot=order.slot,
        total=float(order.total),
        customer_name=order.customer_name,
        student_code=order.student_code,
        items=[
            OrderItemOut(
                product_id=it.product_id,
                product_name=it.product_name,
                unit_price=float(it.unit_price),
                quantity=it.quantity,
            )
            for it in order.items
        ],
    )


# ---------- POST /orders/{number}/cancel (CF-PEDI-0008) ----------

@app.post("/orders/{number}/cancel", response_model=OrderOut)
async def cancel_order(
    number: str,
    body: OrderCancel,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an advance order in RECEIVED status (CF-PEDI-0008)."""
    if not body.student_code or not body.student_code.strip():
        raise HTTPException(status_code=400, detail="Complete los campos obligatorios")

    try:
        num = int(number)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    now_bogota = get_now_bogota()
    today = now_bogota.date()

    stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .where(
            Order.date == today,
            Order.number == num,
        )
        .with_for_update()
    )
    res = await db.execute(stmt)
    order = res.scalars().first()

    if not order:
        raise HTTPException(
            status_code=404,
            detail="No se encontró un pedido con ese número",
        )

    if order.student_code != body.student_code.strip():
        raise HTTPException(
            status_code=403,
            detail="El código estudiantil no coincide con el pedido",
        )

    if order.status in ("READY", "DELIVERED"):
        raise HTTPException(
            status_code=409,
            detail="Tu pedido ya fue preparado y no se puede cancelar",
        )

    if order.status == "CANCELLED":
        raise HTTPException(
            status_code=409,
            detail="Este pedido fue cancelado",
        )

    # Return reserved items to menu-ms before cancelling
    raw_items = [
        {"product_id": it.product_id, "quantity": it.quantity}
        for it in order.items
    ]

    # If menu-ms fails, raise_on_error=True raises MenuUnavailableError (503).
    # Since commit is not called, order remains RECEIVED in DB.
    await release_products(raw_items, raise_on_error=True)

    order.status = "CANCELLED"
    await db.commit()
    await db.refresh(order)

    return OrderOut(
        number=f"{order.number:03d}",
        status=order.status,
        slot=order.slot,
        total=float(order.total),
        customer_name=order.customer_name,
        student_code=order.student_code,
        items=[
            OrderItemOut(
                product_id=it.product_id,
                product_name=it.product_name,
                unit_price=float(it.unit_price),
                quantity=it.quantity,
            )
            for it in order.items
        ],
    )


# ---------- GET /sales/today (CF-PEDI-0007) ----------

@app.get("/sales/today", response_model=SalesTodayOut)
async def get_sales_today(db: AsyncSession = Depends(get_db)):
    """Total sales for today broken down by payment method (DELIVERED only)."""
    now_bogota = get_now_bogota()
    today = now_bogota.date()

    stmt = (
        select(Order.payment_method, func.sum(Order.total), func.count(Order.id))
        .where(
            Order.date == today,
            Order.status == "DELIVERED",
        )
        .group_by(Order.payment_method)
    )
    res = await db.execute(stmt)
    rows = res.all()

    cash_total = 0.0
    transfer_total = 0.0
    total_count = 0

    for pm, total_val, cnt in rows:
        total_count += cnt
        if pm == "CASH":
            cash_total += float(total_val)
        elif pm == "TRANSFER":
            transfer_total += float(total_val)

    grand_total = cash_total + transfer_total

    return SalesTodayOut(
        date=today.isoformat(),
        cash=cash_total,
        transfer=transfer_total,
        total=grand_total,
        count=total_count,
    )



# ---------- GET / PUT /config (CF-PEDI-0009, Section 8.2) ----------

@app.get("/config", response_model=ConfigOut)
async def get_config(db: AsyncSession = Depends(get_db)):
    """Retrieve current system configuration (slot_capacity, start_time, end_time)."""
    configs = await get_system_config(db)
    return ConfigOut(
        slot_capacity=int(configs.get("slot_capacity", "10")),
        start_time=configs.get("start_time", "11:30"),
        end_time=configs.get("end_time", "14:00"),
    )


@app.put("/config", response_model=ConfigOut)
async def update_config(body: ConfigUpdate, db: AsyncSession = Depends(get_db)):
    """Update slot_capacity, start_time, and/or end_time."""
    current_configs = await get_system_config(db)
    current_capacity = int(current_configs.get("slot_capacity", "10"))
    current_start = current_configs.get("start_time", "11:30")
    current_end = current_configs.get("end_time", "14:00")

    target_capacity = body.slot_capacity if body.slot_capacity is not None else current_capacity
    target_start = body.start_time.strip() if body.start_time is not None else current_start
    target_end = body.end_time.strip() if body.end_time is not None else current_end

    if target_capacity <= 0:
        raise HTTPException(
            status_code=400,
            detail="El cupo debe ser un número entero mayor a 0",
        )

    try:
        start_t = datetime.strptime(target_start, "%H:%M").time()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="La hora de inicio debe tener formato HH:MM",
        )

    try:
        end_t = datetime.strptime(target_end, "%H:%M").time()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="La hora de fin debe tener formato HH:MM",
        )

    if start_t >= end_t:
        raise HTTPException(
            status_code=400,
            detail="La hora de inicio debe ser menor a la hora de fin",
        )

    updates = {
        "slot_capacity": str(target_capacity),
        "start_time": target_start,
        "end_time": target_end,
    }
    for k, v in updates.items():
        cfg = await db.get(Config, k)
        if cfg:
            cfg.value = v
        else:
            db.add(Config(key=k, value=v))

    await db.commit()

    return ConfigOut(
        slot_capacity=target_capacity,
        start_time=target_start,
        end_time=target_end,
    )


