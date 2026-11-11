"""HTTP client for communicating with menu-ms.

Handles reservation, release, timeouts and error mapping.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

import httpx

logger = logging.getLogger(__name__)

MENU_URL: str = os.getenv("MENU_URL", "http://menu-ms:8000")
TIMEOUT_SECONDS: float = 3.0


class MenuUnavailableError(Exception):
    """Raised when menu-ms is down, unreachable, or times out."""


class ProductSoldOutError(Exception):
    """Raised when a product is sold out or lacks sufficient units."""


class MenuClientError(Exception):
    """Raised on other menu-ms client/server errors."""


async def reserve_products(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Call menu-ms POST /products/reserve with a 3-second timeout.

    Returns the list of reserved products with names and prices.
    Raises MenuUnavailableError if menu-ms fails to respond within 3 seconds.
    Raises ProductSoldOutError if any product is sold out or out of stock.
    """
    url = f"{MENU_URL}/products/reserve"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=items)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError) as exc:
        logger.error(f"Failed to communicate with menu-ms at {url}: {exc}")
        raise MenuUnavailableError(
            "No fue posible verificar el menú, intenta de nuevo en unos minutos"
        ) from exc

    if resp.status_code == 200:
        return resp.json()

    # Handle error responses from menu-ms
    try:
        err_data = resp.json()
        detail = err_data.get("detail", "Error en el menú")
    except Exception:
        detail = resp.text

    if resp.status_code == 409 or (resp.status_code == 400 and ("agotado" in detail or "unidades" in detail)):
        raise ProductSoldOutError(detail)

    if resp.status_code >= 500:
        raise MenuUnavailableError(
            "No fue posible verificar el menú, intenta de nuevo en unos minutos"
        )

    raise MenuClientError(detail)


async def release_products(items: List[Dict[str, Any]]) -> None:
    """Compensating action: call menu-ms POST /products/release to return units.

    Best-effort, will not raise exception to the caller.
    """
    url = f"{MENU_URL}/products/release"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            await client.post(url, json=items)
    except Exception as exc:
        logger.error(f"Compensating action release_products failed: {exc}")
