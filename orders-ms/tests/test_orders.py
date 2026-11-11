"""Tests for orders-ms Sprint 2 — CF-PEDI-0001, CF-PEDI-0002.

Covers:
- CF-PEDI-0001 scenarios (valid order, empty order, sold out product, menu-ms down, saga compensation)
- CF-PEDI-0002 scenarios (001 numbering, daily reset, concurrency with 20 simultaneous orders on PostgreSQL)
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.menu_client import MenuUnavailableError, ProductSoldOutError
from app.models import Order


# ====================================================================
# CF-PEDI-0001 · Hacer un pedido
# ====================================================================

class TestCreateOrder:
    """CF-PEDI-0001 — place advance order."""

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario1_valid_order(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 1: Pedido válido con productos disponibles → 201 y estado Recibido."""
        mock_reserve.return_value = [
            {
                "product_id": 1,
                "product_name": "Almuerzo corriente",
                "unit_price": 12000.0,
                "quantity": 1,
            },
            {
                "product_id": 4,
                "product_name": "Jugo de mora",
                "unit_price": 2500.0,
                "quantity": 2,
            },
        ]

        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:15",
                "items": [
                    {"product_id": 1, "quantity": 1},
                    {"product_id": 4, "quantity": 2},
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["number"] == "001"
        assert data["status"] == "RECEIVED"
        assert data["slot"] == "12:15"
        assert data["total"] == 17000.0
        assert data["customer_name"] == "Michell Caicedo"
        # Criterio 1: resumen con productos, cantidades y total
        assert data["items"] is not None
        assert len(data["items"]) == 2
        assert data["items"][0]["product_name"] == "Almuerzo corriente"
        assert data["items"][0]["quantity"] == 1
        assert data["items"][0]["unit_price"] == 12000.0
        assert data["items"][1]["product_name"] == "Jugo de mora"
        assert data["items"][1]["quantity"] == 2
        assert data["items"][1]["unit_price"] == 2500.0

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario1_summary_contains_product_details(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 1: Resumen con productos, cantidades y valor total."""
        mock_reserve.return_value = [
            {
                "product_id": 10,
                "product_name": "Bandeja paisa",
                "unit_price": 16000.0,
                "quantity": 3,
            }
        ]

        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Carlos Gomez",
                "student_code": "123456",
                "slot": "13:00",
                "items": [{"product_id": 10, "quantity": 3}],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["items"] is not None
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["product_id"] == 10
        assert item["product_name"] == "Bandeja paisa"
        assert item["quantity"] == 3
        assert item["unit_price"] == 16000.0
        assert data["total"] == 48000.0

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_order_preserves_unit_price_after_menu_price_change(
        self, mock_reserve: AsyncMock, client: AsyncClient, db_session
    ):
        """Un pedido registrado conserva su unit_price aunque después cambie el precio del producto."""
        # 1. Registrar pedido cuando el precio es 12000
        mock_reserve.return_value = [
            {
                "product_id": 1,
                "product_name": "Almuerzo corriente",
                "unit_price": 12000.0,
                "quantity": 1,
            }
        ]

        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Laura",
                "student_code": "987654",
                "slot": "12:30",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["items"][0]["unit_price"] == 12000.0
        assert data["total"] == 12000.0

        # 2. Consultar directamente en BD para verificar persistencia del snapshot
        from app.models import OrderItem
        res = await db_session.execute(select(OrderItem).where(OrderItem.product_id == 1))
        persisted_item = res.scalars().first()
        assert float(persisted_item.unit_price) == 12000.0

        # 3. Simular que el menú cambió el precio a 15000 para pedidos nuevos
        mock_reserve.return_value = [
            {
                "product_id": 1,
                "product_name": "Almuerzo corriente",
                "unit_price": 15000.0,
                "quantity": 1,
            }
        ]
        resp_new = await client.post(
            "/orders",
            json={
                "customer_name": "Andres",
                "student_code": "555555",
                "slot": "12:45",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp_new.status_code == 201
        assert resp_new.json()["items"][0]["unit_price"] == 15000.0

        # El primer pedido conserva intacto su unit_price histórico (12000)
        res_old = await db_session.execute(select(OrderItem).where(OrderItem.order_id == int(persisted_item.order_id)))
        old_item = res_old.scalars().first()
        assert float(old_item.unit_price) == 12000.0

    @pytest.mark.asyncio
    async def test_scenario2_empty_order_items(self, client: AsyncClient):
        """Esc. 2: Pedido sin productos (lista vacía) → 400 'Agrega al menos un producto'."""
        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:15",
                "items": [],
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Agrega al menos un producto"

    @pytest.mark.asyncio
    async def test_scenario2_missing_items_field(self, client: AsyncClient):
        """Esc. 2: Pedido sin campo items → 400 'Agrega al menos un producto'."""
        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:15",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Agrega al menos un producto"

    @pytest.mark.asyncio
    async def test_missing_mandatory_customer_fields(self, client: AsyncClient):
        """Campos obligatorios vacíos (nombre, código o slot) → 400 'Complete los campos obligatorios'."""
        # Falta customer_name
        resp = await client.post(
            "/orders",
            json={
                "student_code": "461224000",
                "slot": "12:15",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Complete los campos obligatorios"

        # Falta student_code
        resp2 = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "slot": "12:15",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp2.status_code == 400
        assert resp2.json()["detail"] == "Complete los campos obligatorios"

        # Falta slot
        resp3 = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp3.status_code == 400
        assert resp3.json()["detail"] == "Complete los campos obligatorios"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario3_sold_out_product_at_confirmation(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 3: Producto agotado al confirmar → 409 e indica cuál producto se agotó."""
        mock_reserve.side_effect = ProductSoldOutError('"Arroz con pollo" está agotado')

        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:15",
                "items": [{"product_id": 2, "quantity": 1}],
            },
        )
        assert resp.status_code == 409
        assert "está agotado" in resp.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario4_menu_unavailable(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 4: menu-ms no responde o timeout 3s → 503 con mensaje exacto."""
        mock_reserve.side_effect = MenuUnavailableError(
            "No fue posible verificar el menú, intenta de nuevo en unos minutos"
        )

        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:15",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp.status_code == 503
        assert (
            resp.json()["detail"]
            == "No fue posible verificar el menú, intenta de nuevo en unos minutos"
        )

    @pytest.mark.asyncio
    @patch("app.main.release_products")
    @patch("app.main.reserve_products")
    async def test_saga_compensation_on_db_failure(
        self, mock_reserve: AsyncMock, mock_release: AsyncMock, client: AsyncClient
    ):
        """Si la inserción en BD falla tras reservar, se invoca release_products (compensación)."""
        mock_reserve.return_value = [
            {
                "product_id": 1,
                "product_name": "Almuerzo corriente",
                "unit_price": 12000.0,
                "quantity": 2,
            }
        ]

        # Simular fallo en db.flush o commit
        with patch("sqlalchemy.ext.asyncio.AsyncSession.flush", side_effect=Exception("Database disk error")):
            resp = await client.post(
                "/orders",
                json={
                    "customer_name": "Michell Caicedo",
                    "student_code": "461224000",
                    "slot": "12:15",
                    "items": [{"product_id": 1, "quantity": 2}],
                },
            )
            assert resp.status_code == 500
            # Validar que release_products fue invocado para liberar las 2 unidades
            mock_release.assert_awaited_once_with([{"product_id": 1, "quantity": 2}])


# ====================================================================
# CF-PEDI-0002 · Número consecutivo diario y concurrencia
# ====================================================================

class TestDailyConsecutiveNumber:
    """CF-PEDI-0002 — sequential number and concurrency."""

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario1_consecutive_number_assignment(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 1: Asignación de consecutivo diario ordenado 001, 002..."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Plato", "unit_price": 10000.0, "quantity": 1}
        ]

        resp1 = await client.post(
            "/orders",
            json={"customer_name": "User 1", "student_code": "111", "slot": "12:00", "items": [{"product_id": 1, "quantity": 1}]},
        )
        assert resp1.status_code == 201
        assert resp1.json()["number"] == "001"

        resp2 = await client.post(
            "/orders",
            json={"customer_name": "User 2", "student_code": "222", "slot": "12:00", "items": [{"product_id": 1, "quantity": 1}]},
        )
        assert resp2.status_code == 201
        assert resp2.json()["number"] == "002"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario2_daily_reset(
        self, mock_reserve: AsyncMock, client: AsyncClient, db_session
    ):
        """Esc. 2: El primer pedido de un nuevo día reinicia en 001."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Plato", "unit_price": 10000.0, "quantity": 1}
        ]

        # Insertar un pedido ficticio de ayer con número 10
        yesterday = date.today() - timedelta(days=1)
        old_order = Order(
            date=yesterday,
            number=10,
            customer_name="Yesterday User",
            student_code="999",
            slot="12:00",
            status="DELIVERED",
            total=10000.0,
        )
        db_session.add(old_order)
        await db_session.commit()

        # Hoy debe iniciar en 001
        resp = await client.post(
            "/orders",
            json={"customer_name": "Today User", "student_code": "333", "slot": "12:15", "items": [{"product_id": 1, "quantity": 1}]},
        )
        assert resp.status_code == 201
        assert resp.json()["number"] == "001"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario3_concurrency_20_simultaneous_orders(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 3 / CP-07: 20 pedidos enviados simultáneamente contra PostgreSQL real.

        Usa pg_advisory_xact_lock para exclusión mutua.
        Todos deben completarse con 201 y recibir números únicos de 001 a 020.
        """
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        async def place_order(i: int):
            return await client.post(
                "/orders",
                json={
                    "customer_name": f"Estudiante {i}",
                    "student_code": f"461224{i:03d}",
                    "slot": "12:30",
                    "items": [{"product_id": 1, "quantity": 1}],
                },
            )

        # Lanzar 20 peticiones concurrentemente
        tasks = [place_order(i) for i in range(1, 21)]
        responses = await asyncio.gather(*tasks)

        # Verificar que todos tuvieron éxito
        for r in responses:
            assert r.status_code == 201, f"Respuesta fallida: {r.status_code} - {r.text}"

        numbers = [r.json()["number"] for r in responses]
        assert len(numbers) == 20

        # Ningún número duplicado
        assert len(set(numbers)) == 20, f"Números duplicados detectados: {numbers}"

        # Conjunto exacto de 001 a 020
        expected = {f"{n:03d}" for n in range(1, 21)}
        assert set(numbers) == expected
