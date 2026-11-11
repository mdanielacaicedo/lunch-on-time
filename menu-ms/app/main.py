"""menu-ms · FastAPI application.

Endpoints for Sprints 1-5 (CF-MENU-0001 through CF-MENU-0006).
Sprint 1 activates: POST /products, GET /menu, PATCH /products/{id}/status,
GET /products/{id} and GET /health.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, init_db
from app.models import Product
from app.schemas import (
    CategoryEnum,
    MenuItemOut,
    PriceUpdate,
    ProductCreate,
    ProductOut,
    StatusUpdate,
    StockUpdate,
)


# ---------- lifespan ----------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="menu-ms",
    description="Microservicio de menú — Almuerzo a Tiempo",
    lifespan=lifespan,
)


# ---------- custom validation error handler ----------
# Pydantic raises 422 by default; we need 400 with the Spanish message from
# the acceptance criteria.

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return 400 with the Spanish validation message instead of 422."""
    for error in exc.errors():
        msg = error.get("msg", "")
        # Surface our custom validator messages
        if "El precio debe ser un número mayor a 0" in msg:
            return JSONResponse(
                status_code=400,
                content={"detail": "El precio debe ser un número mayor a 0"},
            )
        if "Las unidades deben ser un número mayor o igual a 0" in msg:
            return JSONResponse(
                status_code=400,
                content={"detail": "Las unidades deben ser un número mayor o igual a 0"},
            )
    # Fallback: generic required-fields message
    return JSONResponse(
        status_code=400,
        content={"detail": "Complete los campos obligatorios"},
    )


# ---------- health ----------

@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- POST /products  (CF-MENU-0001) ----------

@app.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductCreate, db: AsyncSession = Depends(get_db)):
    # Scenario 2 — required fields
    missing: list[str] = []
    if not body.name or not body.name.strip():
        missing.append("nombre")
    if body.category is None:
        missing.append("categoría")
    if body.price is None:
        missing.append("precio")
    if missing:
        raise HTTPException(status_code=400, detail="Complete los campos obligatorios")

    # Scenario 3 — price validation is handled by Pydantic; if we get here
    # the price is already > 0.  But guard just in case:
    if body.price <= 0:
        raise HTTPException(
            status_code=400,
            detail="El precio debe ser un número mayor a 0",
        )

    # Scenario 4 — duplicate name (case-insensitive)
    existing = await db.execute(
        select(Product).where(func.lower(Product.name) == body.name.strip().lower())
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail="Ya existe un producto con ese nombre",
        )

    product = Product(
        name=body.name.strip(),
        category=body.category.value,
        price=body.price,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)
    return product


# ---------- GET /menu  (CF-MENU-0002, CF-MENU-0006) ----------

@app.get("/menu")
async def get_menu(
    category: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Product)
    if category and category != "all":
        stmt = stmt.where(Product.category == category)
    result = await db.execute(stmt.order_by(Product.name))
    products = result.scalars().all()

    if not products:
        # CF-MENU-0002 scenario 3 / CF-MENU-0006 scenario 2
        if category and category != "all":
            return {
                "products": [],
                "message": "No hay productos en esta categoría hoy",
            }
        return {
            "products": [],
            "message": "La cafetería aún no ha publicado el menú de hoy",
        }

    return {
        "products": [MenuItemOut.model_validate(p) for p in products],
        "message": None,
    }


# ---------- GET /products/{id}  (support) ----------

@app.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return product


# ---------- PATCH /products/{id}/status  (CF-MENU-0003) ----------

@app.patch("/products/{product_id}/status", response_model=ProductOut)
async def update_status(
    product_id: int,
    body: StatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    product.status = body.status.value
    await db.commit()
    await db.refresh(product)
    return product


# ---------- PATCH /products/{id}/price  (CF-MENU-0004, Sprint 2) ----------

@app.patch("/products/{product_id}/price", response_model=ProductOut)
async def update_price(
    product_id: int,
    body: PriceUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    product.price = body.price
    await db.commit()
    await db.refresh(product)
    return product


# ---------- PUT /products/{id}/stock  (CF-MENU-0005, Sprint 5) ----------

@app.put("/products/{product_id}/stock", response_model=ProductOut)
async def update_stock(
    product_id: int,
    body: StockUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if product is None:
        raise HTTPException(status_code=404, detail="Producto no encontrado")

    product.stock = body.stock
    if body.stock == 0:
        product.status = "SOLD_OUT"
    elif product.status == "SOLD_OUT":
        product.status = "AVAILABLE"
    await db.commit()
    await db.refresh(product)
    return product


# ---------- POST /products/reserve  (internal, Sprint 2+) ----------

@app.post("/products/reserve")
async def reserve_products(items: List[dict], db: AsyncSession = Depends(get_db)):
    """Called by orders-ms to verify availability and deduct stock.

    Atomic reserve (all-or-nothing). Uses conditional UPDATE for stock.
    Each item: {"product_id": int, "quantity": int}
    Returns list of products with name and price for the order snapshot.
    """
    reserved: list[dict] = []
    for item in items:
        pid = item.get("product_id")
        qty = item.get("quantity", 1)

        result = await db.execute(select(Product).where(Product.id == pid))
        product = result.scalars().first()
        if product is None:
            await db.rollback()
            raise HTTPException(
                status_code=404,
                detail=f"El producto con id {pid} no existe",
            )
        if product.status == "SOLD_OUT":
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f'"{product.name}" está agotado',
            )

        # Deduct stock if tracked using conditional check
        if product.stock is not None:
            prod_name = product.name
            if product.stock < qty:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f'No hay suficientes unidades de "{prod_name}"',
                )
            product.stock -= qty
            if product.stock == 0:
                product.status = "SOLD_OUT"

        reserved.append(
            {
                "product_id": product.id,
                "product_name": product.name,
                "unit_price": float(product.price),
                "quantity": qty,
            }
        )

    await db.commit()
    return reserved


# ---------- POST /products/release  (internal, Sprint 2+) ----------

@app.post("/products/release")
async def release_products(items: List[dict], db: AsyncSession = Depends(get_db)):
    """Called by orders-ms to return stock on cancellation or compensation.

    Each item: {"product_id": int, "quantity": int}
    """
    for item in items:
        pid = item.get("product_id")
        qty = item.get("quantity", 1)
        result = await db.execute(select(Product).where(Product.id == pid))
        product = result.scalars().first()
        if product is None:
            continue
        if product.stock is not None:
            product.stock += qty
            if product.status == "SOLD_OUT" and product.stock > 0:
                product.status = "AVAILABLE"
    await db.commit()
    return {"detail": "ok"}
