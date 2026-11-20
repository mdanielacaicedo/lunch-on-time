"""Tests for menu-ms Sprint 1 — CF-MENU-0001, CF-MENU-0002, CF-MENU-0003.

9 test cases covering all acceptance-criteria scenarios.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


# ====================================================================
# CF-MENU-0001 · Registrar producto
# ====================================================================

class TestCreateProduct:
    """CF-MENU-0001 — register a product."""

    @pytest.mark.asyncio
    async def test_scenario1_successful_registration(self, client: AsyncClient):
        """Esc. 1: nombre, categoría y precio válido → 201 + estado Disponible."""
        resp = await client.post("/products", json={
            "name": "Almuerzo corriente",
            "category": "lunch",
            "price": 12000,
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Almuerzo corriente"
        assert data["category"] == "lunch"
        assert float(data["price"]) == 12000
        assert data["status"] == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_scenario2_missing_required_fields(self, client: AsyncClient):
        """Esc. 2: falta el nombre → 400 'Complete los campos obligatorios'."""
        resp = await client.post("/products", json={
            "category": "lunch",
            "price": 12000,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Complete los campos obligatorios"

    @pytest.mark.asyncio
    async def test_scenario2_missing_category(self, client: AsyncClient):
        """Esc. 2: falta categoría → 400 'Complete los campos obligatorios'."""
        resp = await client.post("/products", json={
            "name": "Jugo",
            "price": 3000,
        })
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Complete los campos obligatorios"

    @pytest.mark.asyncio
    async def test_scenario2_missing_price(self, client: AsyncClient):
        """Esc. 2: falta precio → 400 'Complete los campos obligatorios'."""
        resp = await client.post("/products", json={
            "name": "Jugo",
            "category": "drink",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Complete los campos obligatorios"

    @pytest.mark.asyncio
    async def test_scenario3_price_zero(self, client: AsyncClient):
        """Esc. 3: precio 0 → 400 'El precio debe ser un número mayor a 0'."""
        resp = await client.post("/products", json={
            "name": "Agua",
            "category": "drink",
            "price": 0,
        })
        assert resp.status_code == 400
        assert "El precio debe ser un número mayor a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario3_price_negative(self, client: AsyncClient):
        """Esc. 3: precio −500 → 400 'El precio debe ser un número mayor a 0'."""
        resp = await client.post("/products", json={
            "name": "Agua",
            "category": "drink",
            "price": -500,
        })
        assert resp.status_code == 400
        assert "El precio debe ser un número mayor a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario3_price_string_invalid(self, client: AsyncClient):
        """Esc. 3: precio 'abc' → 400 'El precio debe ser un número mayor a 0'."""
        resp = await client.post("/products", json={
            "name": "Agua",
            "category": "drink",
            "price": "abc",
        })
        assert resp.status_code == 400
        assert "El precio debe ser un número mayor a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario4_duplicate_name(self, client: AsyncClient):
        """Esc. 4: nombre repetido (distinta capitalización) → 409."""
        await client.post("/products", json={
            "name": "Almuerzo corriente",
            "category": "lunch",
            "price": 12000,
        })
        resp = await client.post("/products", json={
            "name": "almuerzo CORRIENTE",
            "category": "lunch",
            "price": 15000,
        })
        assert resp.status_code == 409
        assert resp.json()["detail"] == "Ya existe un producto con ese nombre"


# ====================================================================
# CF-MENU-0002 · Ver menú del día
# ====================================================================

class TestGetMenu:
    """CF-MENU-0002 — view the daily menu."""

    @pytest.mark.asyncio
    async def test_scenario1_menu_with_products(self, client: AsyncClient):
        """Esc. 1: menú con productos → muestra nombre, categoría y precio."""
        await client.post("/products", json={
            "name": "Almuerzo corriente",
            "category": "lunch",
            "price": 12000,
        })
        await client.post("/products", json={
            "name": "Jugo de mora",
            "category": "drink",
            "price": 3000,
        })
        resp = await client.get("/menu")
        assert resp.status_code == 200
        data = resp.json()
        products = data["products"]
        assert len(products) == 2
        names = {p["name"] for p in products}
        assert "Almuerzo corriente" in names
        assert "Jugo de mora" in names
        for p in products:
            assert "name" in p
            assert "category" in p
            assert "price" in p

    @pytest.mark.asyncio
    async def test_scenario2_sold_out_product(self, client: AsyncClient):
        """Esc. 2: producto agotado → aparece con status SOLD_OUT."""
        create_resp = await client.post("/products", json={
            "name": "Empanada",
            "category": "snack",
            "price": 2500,
        })
        product_id = create_resp.json()["id"]
        await client.patch(f"/products/{product_id}/status", json={
            "status": "SOLD_OUT",
        })

        resp = await client.get("/menu")
        assert resp.status_code == 200
        products = resp.json()["products"]
        empanada = [p for p in products if p["name"] == "Empanada"][0]
        assert empanada["status"] == "SOLD_OUT"

    @pytest.mark.asyncio
    async def test_scenario3_empty_menu(self, client: AsyncClient):
        """Esc. 3: sin productos → mensaje 'aún no ha publicado el menú'."""
        resp = await client.get("/menu")
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == []
        assert data["message"] == "La cafetería aún no ha publicado el menú de hoy"


# ====================================================================
# CF-MENU-0003 · Marcar agotado / disponible
# ====================================================================

class TestUpdateStatus:
    """CF-MENU-0003 — toggle product availability."""

    @pytest.mark.asyncio
    async def test_scenario1_mark_sold_out(self, client: AsyncClient):
        """Esc. 1: producto Disponible → marcar Agotado → SOLD_OUT."""
        create_resp = await client.post("/products", json={
            "name": "Arroz con pollo",
            "category": "lunch",
            "price": 14000,
        })
        product_id = create_resp.json()["id"]
        assert create_resp.json()["status"] == "AVAILABLE"

        resp = await client.patch(f"/products/{product_id}/status", json={
            "status": "SOLD_OUT",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "SOLD_OUT"

    @pytest.mark.asyncio
    async def test_scenario2_mark_available_again(self, client: AsyncClient):
        """Esc. 2: producto Agotado → marcar Disponible → AVAILABLE."""
        create_resp = await client.post("/products", json={
            "name": "Bandeja paisa",
            "category": "lunch",
            "price": 16000,
        })
        product_id = create_resp.json()["id"]
        # Mark sold out first
        await client.patch(f"/products/{product_id}/status", json={
            "status": "SOLD_OUT",
        })
        # Mark available again
        resp = await client.patch(f"/products/{product_id}/status", json={
            "status": "AVAILABLE",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "AVAILABLE"


# ====================================================================
# CF-MENU-0004 · Cambiar precio de un producto
# ====================================================================

class TestUpdatePrice:
    """CF-MENU-0004 — update product price."""

    @pytest.mark.asyncio
    async def test_scenario1_successful_price_update(self, client: AsyncClient):
        """Esc. 1: nuevo precio mayor a 0 → 200 y precio actualizado."""
        create_resp = await client.post("/products", json={
            "name": "Sopa del día",
            "category": "lunch",
            "price": 5000,
        })
        product_id = create_resp.json()["id"]

        resp = await client.patch(f"/products/{product_id}/price", json={
            "price": 6000,
        })
        assert resp.status_code == 200
        assert resp.json()["price"] == 6000.0

        # Verificar en el menú
        menu_resp = await client.get("/menu")
        products = menu_resp.json()["products"]
        sopa = [p for p in products if p["id"] == product_id][0]
        assert sopa["price"] == 6000.0

    @pytest.mark.asyncio
    async def test_scenario2_price_zero(self, client: AsyncClient):
        """Esc. 2: nuevo precio 0 → 400 'El precio debe ser un número mayor a 0'."""
        create_resp = await client.post("/products", json={
            "name": "Café",
            "category": "drink",
            "price": 2000,
        })
        product_id = create_resp.json()["id"]

        resp = await client.patch(f"/products/{product_id}/price", json={
            "price": 0,
        })
        assert resp.status_code == 400
        assert "El precio debe ser un número mayor a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario2_price_negative(self, client: AsyncClient):
        """Esc. 2: nuevo precio negativo → 400 'El precio debe ser un número mayor a 0'."""
        create_resp = await client.post("/products", json={
            "name": "Té",
            "category": "drink",
            "price": 2000,
        })
        product_id = create_resp.json()["id"]

        resp = await client.patch(f"/products/{product_id}/price", json={
            "price": -1000,
        })
        assert resp.status_code == 400
        assert "El precio debe ser un número mayor a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario2_price_string_invalid(self, client: AsyncClient):
        """Esc. 2: nuevo precio 'abc' → 400 'El precio debe ser un número mayor a 0'."""
        create_resp = await client.post("/products", json={
            "name": "Gaseosa",
            "category": "drink",
            "price": 3000,
        })
        product_id = create_resp.json()["id"]

        resp = await client.patch(f"/products/{product_id}/price", json={
            "price": "abc",
        })
        assert resp.status_code == 400
        assert "El precio debe ser un número mayor a 0" in resp.json()["detail"]


# ====================================================================
# Reserve & Release (Uso interno de orders-ms y stock)
# ====================================================================

class TestReserveAndRelease:
    """Tests for POST /products/reserve and /products/release."""

    @pytest.mark.asyncio
    async def test_reserve_deducts_stock_and_marks_sold_out_at_zero(self, client: AsyncClient):
        """Reserva descuenta stock y si llega a 0 queda SOLD_OUT."""
        create_resp = await client.post("/products", json={
            "name": "Almuerzo Especial",
            "category": "lunch",
            "price": 15000,
        })
        pid = create_resp.json()["id"]
        # Asignar 2 unidades
        await client.put(f"/products/{pid}/stock", json={"stock": 2})

        # Reservar 2 unidades
        reserve_resp = await client.post("/products/reserve", json=[
            {"product_id": pid, "quantity": 2}
        ])
        assert reserve_resp.status_code == 200
        assert len(reserve_resp.json()) == 1

        # Verificar que el producto quedó SOLD_OUT
        prod_resp = await client.get(f"/products/{pid}")
        assert prod_resp.json()["stock"] == 0
        assert prod_resp.json()["status"] == "SOLD_OUT"

    @pytest.mark.asyncio
    async def test_reserve_insufficient_stock_fails_all_or_nothing(self, client: AsyncClient):
        """Si un producto no alcanza, 409 y no descuenta ninguno (todo o nada)."""
        p1 = (await client.post("/products", json={"name": "Postre A", "category": "snack", "price": 4000})).json()
        p2 = (await client.post("/products", json={"name": "Postre B", "category": "snack", "price": 5000})).json()

        await client.put(f"/products/{p1['id']}/stock", json={"stock": 5})
        await client.put(f"/products/{p2['id']}/stock", json={"stock": 1})

        # Intentar reservar 2 de A y 2 de B (B no alcanza)
        resp = await client.post("/products/reserve", json=[
            {"product_id": p1["id"], "quantity": 2},
            {"product_id": p2["id"], "quantity": 2},
        ])
        assert resp.status_code == 409
        assert "No hay suficientes unidades" in resp.json()["detail"]

        # Verificar que de A no se descontó nada (sigue en 5)
        check_p1 = (await client.get(f"/products/{p1['id']}")).json()
        assert check_p1["stock"] == 5

    @pytest.mark.asyncio
    async def test_release_returns_stock_and_restores_available(self, client: AsyncClient):
        """Release devuelve unidades y si estaba SOLD_OUT lo vuelve a AVAILABLE."""
        create_resp = await client.post("/products", json={
            "name": "Jugo Natural",
            "category": "drink",
            "price": 3500,
        })
        pid = create_resp.json()["id"]
        await client.put(f"/products/{pid}/stock", json={"stock": 1})

        # Reservar 1 -> queda en 0 y SOLD_OUT
        await client.post("/products/reserve", json=[{"product_id": pid, "quantity": 1}])
        check = (await client.get(f"/products/{pid}")).json()
        assert check["status"] == "SOLD_OUT"
        assert check["stock"] == 0

        # Liberar 1 -> vuelve a stock 1 y AVAILABLE
        rel_resp = await client.post("/products/release", json=[{"product_id": pid, "quantity": 1}])
        assert rel_resp.status_code == 200

        check_after = (await client.get(f"/products/{pid}")).json()
        assert check_after["stock"] == 1
        assert check_after["status"] == "AVAILABLE"


# ====================================================================
# CF-MENU-0005 · Registrar unidades del día (PUT /products/{id}/stock)
# ====================================================================

class TestStockUpdate:
    """CF-MENU-0005 — manage daily stock units."""

    @pytest.mark.asyncio
    async def test_scenario1_set_stock_valid_integer(self, client: AsyncClient):
        """Esc. 1: entero mayor o igual a 0 → guarda la cantidad y retorna 200."""
        create_resp = await client.post("/products", json={
            "name": "Bandeja ejecutiva",
            "category": "lunch",
            "price": 13000,
        })
        pid = create_resp.json()["id"]

        resp = await client.put(f"/products/{pid}/stock", json={"stock": 15})
        assert resp.status_code == 200
        data = resp.json()
        assert data["stock"] == 15
        assert data["status"] == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_scenario1_set_stock_null_unmanaged(self, client: AsyncClient):
        """Esc. 1: stock null / vacío → sin control de unidades (stock = None)."""
        create_resp = await client.post("/products", json={
            "name": "Agua en botella",
            "category": "drink",
            "price": 2000,
        })
        pid = create_resp.json()["id"]

        # Primero asignar stock
        await client.put(f"/products/{pid}/stock", json={"stock": 10})
        check = (await client.get(f"/products/{pid}")).json()
        assert check["stock"] == 10

        # Luego quitar control con null
        resp = await client.put(f"/products/{pid}/stock", json={"stock": None})
        assert resp.status_code == 200
        assert resp.json()["stock"] is None

    @pytest.mark.asyncio
    async def test_scenario1_set_stock_empty_string_unmanaged(self, client: AsyncClient):
        """Esc. 1: stock con string vacío "" → sin control de unidades (stock = None)."""
        create_resp = await client.post("/products", json={
            "name": "Gaseosa lata",
            "category": "drink",
            "price": 3000,
        })
        pid = create_resp.json()["id"]

        resp = await client.put(f"/products/{pid}/stock", json={"stock": ""})
        assert resp.status_code == 200
        assert resp.json()["stock"] is None

    @pytest.mark.asyncio
    async def test_scenario1_set_stock_negative_fails(self, client: AsyncClient):
        """Esc. 1: número negativo → 400 'Las unidades deben ser un número mayor o igual a 0'."""
        create_resp = await client.post("/products", json={
            "name": "Postre de natas",
            "category": "snack",
            "price": 4000,
        })
        pid = create_resp.json()["id"]

        resp = await client.put(f"/products/{pid}/stock", json={"stock": -5})
        assert resp.status_code == 400
        assert "Las unidades deben ser un número mayor o igual a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario1_set_stock_invalid_string_fails(self, client: AsyncClient):
        """Esc. 1: valor alfanumérico no entero → 400."""
        create_resp = await client.post("/products", json={
            "name": "Galleta festival",
            "category": "snack",
            "price": 1500,
        })
        pid = create_resp.json()["id"]

        resp = await client.put(f"/products/{pid}/stock", json={"stock": "abc"})
        assert resp.status_code == 400
        assert "Las unidades deben ser un número mayor o igual a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario1_set_stock_float_fails(self, client: AsyncClient):
        """Esc. 1: número decimal/float → 400."""
        create_resp = await client.post("/products", json={
            "name": "Torta de chocolate",
            "category": "snack",
            "price": 4500,
        })
        pid = create_resp.json()["id"]

        resp = await client.put(f"/products/{pid}/stock", json={"stock": 3.5})
        assert resp.status_code == 400
        assert "Las unidades deben ser un número mayor o igual a 0" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_scenario3_stock_zero_marks_sold_out(self, client: AsyncClient):
        """Esc. 3: si las unidades restantes llegan a 0 → producto pasa a SOLD_OUT."""
        create_resp = await client.post("/products", json={
            "name": "Arroz con carne",
            "category": "lunch",
            "price": 14000,
        })
        pid = create_resp.json()["id"]
        assert create_resp.json()["status"] == "AVAILABLE"

        resp = await client.put(f"/products/{pid}/stock", json={"stock": 0})
        assert resp.status_code == 200
        assert resp.json()["stock"] == 0
        assert resp.json()["status"] == "SOLD_OUT"

    @pytest.mark.asyncio
    async def test_scenario4_stock_positive_restores_available(self, client: AsyncClient):
        """Esc. 4: producto SOLD_OUT pasa a AVAILABLE al registrar stock > 0."""
        create_resp = await client.post("/products", json={
            "name": "Limonada",
            "category": "drink",
            "price": 2500,
        })
        pid = create_resp.json()["id"]
        # Marcar en 0 y SOLD_OUT
        await client.put(f"/products/{pid}/stock", json={"stock": 0})
        check = (await client.get(f"/products/{pid}")).json()
        assert check["status"] == "SOLD_OUT"

        # Asignar nuevas unidades
        resp = await client.put(f"/products/{pid}/stock", json={"stock": 8})
        assert resp.status_code == 200
        assert resp.json()["stock"] == 8
        assert resp.json()["status"] == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_scenario4_stock_null_restores_available(self, client: AsyncClient):
        """Esc. 4: producto SOLD_OUT por stock 0 pasa a AVAILABLE al quitar límite (stock = null)."""
        create_resp = await client.post("/products", json={
            "name": "Chocoramo",
            "category": "snack",
            "price": 2800,
        })
        pid = create_resp.json()["id"]
        await client.put(f"/products/{pid}/stock", json={"stock": 0})
        check = (await client.get(f"/products/{pid}")).json()
        assert check["status"] == "SOLD_OUT"

        resp = await client.put(f"/products/{pid}/stock", json={"stock": None})
        assert resp.status_code == 200
        assert resp.json()["stock"] is None
        assert resp.json()["status"] == "AVAILABLE"

    @pytest.mark.asyncio
    async def test_stock_update_nonexistent_product_404(self, client: AsyncClient):
        """Actualizar stock de producto inexistente retorna 404."""
        resp = await client.put("/products/99999/stock", json={"stock": 5})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Producto no encontrado"


# ====================================================================
# CF-MENU-0006 · Filtrar menú por categoría (GET /menu?category=...)
# ====================================================================

class TestFilterByCategory:
    """CF-MENU-0006 — filter daily menu by category."""

    @pytest.mark.asyncio
    async def test_scenario1_filter_by_category_lunch(self, client: AsyncClient):
        """Esc. 1: filtrar por lunch muestra sólo almuerzos."""
        await client.post("/products", json={"name": "Ajiaco", "category": "lunch", "price": 14000})
        await client.post("/products", json={"name": "Jugo de maracuyá", "category": "drink", "price": 3000})
        await client.post("/products", json={"name": "Papas fritas", "category": "snack", "price": 2500})

        resp = await client.get("/menu?category=lunch")
        assert resp.status_code == 200
        data = resp.json()
        products = data["products"]
        assert len(products) == 1
        assert products[0]["name"] == "Ajiaco"
        assert products[0]["category"] == "lunch"

    @pytest.mark.asyncio
    async def test_scenario1_filter_by_category_drink_and_snack(self, client: AsyncClient):
        """Esc. 1: filtrar por drink y por snack muestra sólo los productos respectivos."""
        await client.post("/products", json={"name": "Almuerzo casero", "category": "lunch", "price": 12000})
        await client.post("/products", json={"name": "Gaseosa 350ml", "category": "drink", "price": 3000})
        await client.post("/products", json={"name": "Brownie", "category": "snack", "price": 2000})

        drink_resp = await client.get("/menu?category=drink")
        assert drink_resp.status_code == 200
        drinks = drink_resp.json()["products"]
        assert len(drinks) == 1
        assert drinks[0]["name"] == "Gaseosa 350ml"

        snack_resp = await client.get("/menu?category=snack")
        assert snack_resp.status_code == 200
        snacks = snack_resp.json()["products"]
        assert len(snacks) == 1
        assert snacks[0]["name"] == "Brownie"

    @pytest.mark.asyncio
    async def test_scenario2_empty_category_message(self, client: AsyncClient):
        """Esc. 2: categoría sin productos → mensaje 'No hay productos en esta categoría hoy'."""
        # Menú con almuerzo pero sin bebidas
        await client.post("/products", json={"name": "Sancocho", "category": "lunch", "price": 15000})

        resp = await client.get("/menu?category=drink")
        assert resp.status_code == 200
        data = resp.json()
        assert data["products"] == []
        assert data["message"] == "No hay productos en esta categoría hoy"

    @pytest.mark.asyncio
    async def test_scenario3_remove_filter_all_shows_full_menu(self, client: AsyncClient):
        """Esc. 3: categoría 'all' o sin parámetro muestra el menú completo."""
        await client.post("/products", json={"name": "Almuerzo", "category": "lunch", "price": 12000})
        await client.post("/products", json={"name": "Jugo", "category": "drink", "price": 2500})

        # Con category=all
        resp_all = await client.get("/menu?category=all")
        assert resp_all.status_code == 200
        assert len(resp_all.json()["products"]) == 2

        # Sin query parameter
        resp_none = await client.get("/menu")
        assert resp_none.status_code == 200
        assert len(resp_none.json()["products"]) == 2

