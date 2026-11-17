"""Tests for orders-ms Sprint 2 — CF-PEDI-0001, CF-PEDI-0002.

Covers:
- CF-PEDI-0001 scenarios (valid order, empty order, sold out product, menu-ms down, saga compensation)
- CF-PEDI-0002 scenarios (001 numbering, daily reset, concurrency with 20 simultaneous orders on PostgreSQL)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.main import BOGOTA_TZ
from app.menu_client import MenuUnavailableError, ProductSoldOutError
from app.models import Config, Order, OrderItem


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


# ====================================================================
# CF-PEDI-0003 · Franjas de recogida (Pickup Slots)
# ====================================================================

class TestPickupSlots:
    """CF-PEDI-0003 — 15-minute pickup slots and validation."""

    @pytest.mark.asyncio
    async def test_scenario1_slots_returned_with_status(self, client: AsyncClient):
        """Esc. 1: GET /slots devuelve franjas de 15 min entre start_time y end_time.

        A las 11:00 AM (simulada), todas las franjas (11:30 a 13:45) son futuras (>= 15 min)
        y tienen estado 'available'.
        """
        resp = await client.get("/slots")
        assert resp.status_code == 200
        slots = resp.json()
        assert len(slots) == 10

        expected_times = [
            "11:30", "11:45", "12:00", "12:15", "12:30",
            "12:45", "13:00", "13:15", "13:30", "13:45",
        ]
        assert [s["slot"] for s in slots] == expected_times
        for s in slots:
            assert s["status"] == "available"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario1_valid_slot_order_created(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 1: Pedido con franja válida disponible guarda la franja y responde 201."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        resp = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:15",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["slot"] == "12:15"
        assert data["status"] == "RECEIVED"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario2_past_slot_status_and_rejection(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 2: Franja pasada o que empieza en menos de 15 minutos queda 'past' y se rechaza."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        # Simular que son las 12:05 en Bogotá
        simulated_now = datetime.combine(date.today(), time(12, 5), tzinfo=BOGOTA_TZ)
        with patch("app.main.get_now_bogota", return_value=simulated_now):
            # GET /slots a las 12:05:
            # - 11:30, 11:45, 12:00 ya pasaron -> past
            # - 12:15 empieza en 10 min (< 15 min) -> past
            # - 12:30 empieza en 25 min (>= 15 min) -> available
            resp = await client.get("/slots")
            assert resp.status_code == 200
            slots = {s["slot"]: s["status"] for s in resp.json()}

            assert slots["11:30"] == "past"
            assert slots["11:45"] == "past"
            assert slots["12:00"] == "past"
            assert slots["12:15"] == "past"
            assert slots["12:30"] == "available"

            # POST /orders con franja que ya pasó (11:30) -> 400
            resp_past = await client.post(
                "/orders",
                json={
                    "customer_name": "Estudiante",
                    "student_code": "123",
                    "slot": "11:30",
                    "items": [{"product_id": 1, "quantity": 1}],
                },
            )
            assert resp_past.status_code == 400
            assert resp_past.json()["detail"] == "Selecciona una hora de recogida"

            # POST /orders con franja que empieza en menos de 15 min (12:15) -> 400
            resp_soon = await client.post(
                "/orders",
                json={
                    "customer_name": "Estudiante",
                    "student_code": "123",
                    "slot": "12:15",
                    "items": [{"product_id": 1, "quantity": 1}],
                },
            )
            assert resp_soon.status_code == 400
            assert resp_soon.json()["detail"] == "Selecciona una hora de recogida"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario3_empty_and_unsupported_slot_rejected(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 3: Franja vacía, con espacios o no generada (ej. 12:07 o 16:00) -> 400 'Selecciona una hora de recogida'."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        # Franja vacía
        resp_empty = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp_empty.status_code == 400
        assert resp_empty.json()["detail"] == "Selecciona una hora de recogida"

        # Franja solo espacios
        resp_spaces = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "   ",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp_spaces.status_code == 400
        assert resp_spaces.json()["detail"] == "Selecciona una hora de recogida"

        # Franja no generada (12:07)
        resp_invalid_time = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "12:07",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp_invalid_time.status_code == 400
        assert resp_invalid_time.json()["detail"] == "Selecciona una hora de recogida"

        # Franja fuera de horario (16:00)
        resp_out_of_bounds = await client.post(
            "/orders",
            json={
                "customer_name": "Michell Caicedo",
                "student_code": "461224000",
                "slot": "16:00",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert resp_out_of_bounds.status_code == 400
        assert resp_out_of_bounds.json()["detail"] == "Selecciona una hora de recogida"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_slot_capacity_full_and_released_on_cancel(
        self, mock_reserve: AsyncMock, client: AsyncClient, db_session
    ):
        """Al alcanzar slot_capacity, la franja pasa a estado 'full' en GET /slots.

        Si un pedido de esa franja se cancela, la franja vuelve a 'available'.
        """
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        # Fijar cupo a 2 para la prueba
        cfg = await db_session.get(Config, "slot_capacity")
        cfg.value = "2"
        await db_session.commit()

        # Pedido 1 en 12:30
        r1 = await client.post(
            "/orders",
            json={
                "customer_name": "User 1",
                "student_code": "111",
                "slot": "12:30",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert r1.status_code == 201

        # Pedido 2 en 12:30 -> alcanza cupo (2)
        r2 = await client.post(
            "/orders",
            json={
                "customer_name": "User 2",
                "student_code": "222",
                "slot": "12:30",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )
        assert r2.status_code == 201

        # Consultar /slots: 12:30 debe estar 'full'
        slots_resp = await client.get("/slots")
        slots_map = {s["slot"]: s["status"] for s in slots_resp.json()}
        assert slots_map["12:30"] == "full"
        assert slots_map["12:45"] == "available"

        # Simular cancelación del pedido 1
        res = await db_session.execute(select(Order).where(Order.number == 1))
        order1 = res.scalars().first()
        order1.status = "CANCELLED"
        await db_session.commit()

        # Ahora el cupo se liberó (1 pedido no cancelado < 2) -> vuelve a 'available'
        slots_resp_after = await client.get("/slots")
        slots_map_after = {s["slot"]: s["status"] for s in slots_resp_after.json()}
        assert slots_map_after["12:30"] == "available"


# ====================================================================
# Ajuste 1 · Prueba de concurrencia de cupo por franja
# ====================================================================

class TestSlotCapacityConcurrency:
    """Ajuste 1: cupo 2 y 5 pedidos simultáneos a la misma franja → solo 2 se registran."""

    @pytest.mark.asyncio
    @patch("app.main.release_products")
    @patch("app.main.reserve_products")
    async def test_concurrency_capacity_2_with_5_simultaneous_orders(
        self, mock_reserve: AsyncMock, mock_release: AsyncMock, client: AsyncClient, db_session
    ):
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        # Fijar cupo a 2
        cfg = await db_session.get(Config, "slot_capacity")
        cfg.value = "2"
        await db_session.commit()

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

        # 5 pedidos simultáneos para la franja 12:30
        tasks = [place_order(i) for i in range(1, 6)]
        responses = await asyncio.gather(*tasks)

        status_codes = [r.status_code for r in responses]
        success_count = status_codes.count(201)
        conflict_count = status_codes.count(409)

        assert success_count == 2, f"Se esperaban 2 éxitos, se obtuvieron: {status_codes}"
        assert conflict_count == 3, f"Se esperaban 3 rechazos 409, se obtuvieron: {status_codes}"

        # Validar mensaje exacto de rechazo
        for r in responses:
            if r.status_code == 409:
                assert r.json()["detail"] == "La franja seleccionada está llena"

        # Validar compensación saga: release_products invocado 3 veces
        assert mock_release.await_count == 3


# ====================================================================
# CF-PEDI-0004 · Listar pedidos pendientes
# ====================================================================

class TestPendingOrders:
    """CF-PEDI-0004 — list received orders ordered by slot and number."""

    @pytest.mark.asyncio
    async def test_scenario2_empty_pending_orders(self, client: AsyncClient):
        """Esc. 2: Sin pedidos pendientes -> lista vacía y message 'No hay pedidos pendientes'."""
        resp = await client.get("/orders?status=RECEIVED")
        assert resp.status_code == 200
        data = resp.json()
        assert data["orders"] == []
        assert data["message"] == "No hay pedidos pendientes"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario1_list_ordered_by_slot_and_number(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 1: Pedidos ordenados primero por franja y luego por número con nombre, productos y cantidades."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo corriente", "unit_price": 12000.0, "quantity": 2},
            {"product_id": 2, "product_name": "Jugo de mora", "unit_price": 2500.0, "quantity": 1},
        ]

        # Pedido 1: slot 13:00 (número 001)
        await client.post(
            "/orders",
            json={
                "customer_name": "Carlos Gomez",
                "student_code": "100",
                "slot": "13:00",
                "items": [{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 1}],
            },
        )

        # Pedido 2: slot 12:15 (número 002)
        await client.post(
            "/orders",
            json={
                "customer_name": "Ana Perez",
                "student_code": "200",
                "slot": "12:15",
                "items": [{"product_id": 1, "quantity": 1}],
            },
        )

        # Pedido 3: slot 12:15 (número 003)
        await client.post(
            "/orders",
            json={
                "customer_name": "Beatriz Rojas",
                "student_code": "300",
                "slot": "12:15",
                "items": [{"product_id": 2, "quantity": 3}],
            },
        )

        resp = await client.get("/orders?status=RECEIVED")
        assert resp.status_code == 200
        data = resp.json()
        orders = data["orders"]
        assert len(orders) == 3
        assert data["message"] is None

        # Orden esperado: 12:15 (002), 12:15 (003), 13:00 (001)
        assert orders[0]["number"] == "002"
        assert orders[0]["slot"] == "12:15"
        assert orders[0]["customer_name"] == "Ana Perez"

        assert orders[1]["number"] == "003"
        assert orders[1]["slot"] == "12:15"
        assert orders[1]["customer_name"] == "Beatriz Rojas"

        assert orders[2]["number"] == "001"
        assert orders[2]["slot"] == "13:00"
        assert orders[2]["customer_name"] == "Carlos Gomez"

        # Verificar detalle de productos y cantidades
        first_items = orders[2]["items"]
        assert len(first_items) == 2
        assert first_items[0]["product_name"] == "Almuerzo corriente"
        assert first_items[0]["quantity"] == 2
        assert first_items[1]["product_name"] == "Jugo de mora"
        assert first_items[1]["quantity"] == 1

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario3_cancelled_orders_excluded(
        self, mock_reserve: AsyncMock, client: AsyncClient, db_session
    ):
        """Esc. 3: Los pedidos cancelados no aparecen en la lista de pendientes."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        # Crear 2 pedidos
        r1 = await client.post(
            "/orders",
            json={"customer_name": "User 1", "student_code": "111", "slot": "12:15", "items": [{"product_id": 1, "quantity": 1}]},
        )
        r2 = await client.post(
            "/orders",
            json={"customer_name": "User 2", "student_code": "222", "slot": "12:30", "items": [{"product_id": 1, "quantity": 1}]},
        )
        assert r1.status_code == 201
        assert r2.status_code == 201

        # Cancelar pedido 1
        res = await db_session.execute(select(Order).where(Order.number == 1))
        order1 = res.scalars().first()
        order1.status = "CANCELLED"
        await db_session.commit()

        # GET /orders?status=RECEIVED solo muestra el pedido 2
        resp = await client.get("/orders?status=RECEIVED")
        assert resp.status_code == 200
        orders = resp.json()["orders"]
        assert len(orders) == 1
        assert orders[0]["number"] == "002"


# ====================================================================
# CF-PEDI-0005 · Marcar pedido como Listo
# ====================================================================

class TestMarkOrderReady:
    """CF-PEDI-0005 — mark order ready (POST /orders/{number}/ready)."""

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario1_mark_order_ready(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Esc. 1: Pedido en 'RECEIVED' pasa a 'READY', sale de pendientes y entra a listos."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        post_resp = await client.post(
            "/orders",
            json={"customer_name": "Michell", "student_code": "461224000", "slot": "12:15", "items": [{"product_id": 1, "quantity": 1}]},
        )
        assert post_resp.status_code == 201
        number = post_resp.json()["number"]

        # Marcar como listo
        ready_resp = await client.post(f"/orders/{number}/ready")
        assert ready_resp.status_code == 200
        assert ready_resp.json()["status"] == "READY"
        assert ready_resp.json()["number"] == number

        # Ya no aparece en RECEIVED
        rec_resp = await client.get("/orders?status=RECEIVED")
        assert len(rec_resp.json()["orders"]) == 0

        # Ahora aparece en READY
        ready_list_resp = await client.get("/orders?status=READY")
        assert ready_list_resp.status_code == 200
        ready_orders = ready_list_resp.json()["orders"]
        assert len(ready_orders) == 1
        assert ready_orders[0]["number"] == number
        assert ready_orders[0]["status"] == "READY"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_ready_order_is_idempotent(
        self, mock_reserve: AsyncMock, client: AsyncClient
    ):
        """Ajuste 4: si ya está READY, devuélvelo sin error (idempotente 200)."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        post_resp = await client.post(
            "/orders",
            json={"customer_name": "User", "student_code": "123", "slot": "12:15", "items": [{"product_id": 1, "quantity": 1}]},
        )
        number = post_resp.json()["number"]

        # Primer llamado -> 200
        r1 = await client.post(f"/orders/{number}/ready")
        assert r1.status_code == 200
        assert r1.json()["status"] == "READY"

        # Segundo llamado -> 200 sin error
        r2 = await client.post(f"/orders/{number}/ready")
        assert r2.status_code == 200
        assert r2.json()["status"] == "READY"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_scenario2_mark_cancelled_order_ready_conflict(
        self, mock_reserve: AsyncMock, client: AsyncClient, db_session
    ):
        """Esc. 2: Marcar Listo un pedido cancelado -> 409 'Este pedido fue cancelado'."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        post_resp = await client.post(
            "/orders",
            json={"customer_name": "User", "student_code": "123", "slot": "12:15", "items": [{"product_id": 1, "quantity": 1}]},
        )
        number = post_resp.json()["number"]

        # Cancelar pedido
        res = await db_session.execute(select(Order).where(Order.number == int(number)))
        order = res.scalars().first()
        order.status = "CANCELLED"
        await db_session.commit()

        # Intentar marcar Listo
        ready_resp = await client.post(f"/orders/{number}/ready")
        assert ready_resp.status_code == 409
        assert ready_resp.json()["detail"] == "Este pedido fue cancelado"

    @pytest.mark.asyncio
    @patch("app.main.reserve_products")
    async def test_mark_delivered_order_ready_conflict(
        self, mock_reserve: AsyncMock, client: AsyncClient, db_session
    ):
        """Ajuste 4: Marcar Listo un pedido DELIVERED -> 409 'Este pedido ya fue entregado'."""
        mock_reserve.return_value = [
            {"product_id": 1, "product_name": "Almuerzo", "unit_price": 12000.0, "quantity": 1}
        ]

        post_resp = await client.post(
            "/orders",
            json={"customer_name": "User", "student_code": "123", "slot": "12:15", "items": [{"product_id": 1, "quantity": 1}],}
        )
        number = post_resp.json()["number"]

        # Marcar como DELIVERED
        res = await db_session.execute(select(Order).where(Order.number == int(number)))
        order = res.scalars().first()
        order.status = "DELIVERED"
        await db_session.commit()

        ready_resp = await client.post(f"/orders/{number}/ready")
        assert ready_resp.status_code == 409
        assert ready_resp.json()["detail"] == "Este pedido ya fue entregado"

    @pytest.mark.asyncio
    async def test_mark_nonexistent_or_invalid_order_ready_404(
        self, client: AsyncClient
    ):
        """Ajuste 3: Pedido inexistente o número no válido -> 404 'No se encontró un pedido con ese número' (nunca 500)."""
        # Número inexistente
        r1 = await client.post("/orders/999/ready")
        assert r1.status_code == 404
        assert r1.json()["detail"] == "No se encontró un pedido con ese número"

        # Número no numérico
        r2 = await client.post("/orders/abc/ready")
        assert r2.status_code == 404
        assert r2.json()["detail"] == "No se encontró un pedido con ese número"

