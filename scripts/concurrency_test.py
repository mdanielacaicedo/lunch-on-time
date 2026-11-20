#!/usr/bin/env python3
"""Concurrency Test Script for Lunch on Time (Almuerzo a Tiempo).

Sends 20 simultaneous order requests via Nginx (http://localhost:8080/api/orders/orders)
targeting the exact same pickup timeslot.

Verifies:
1. Slot capacity is strictly enforced (e.g. capacity = 10 -> exactly 10 accepted, 10 rejected).
2. Unique sequential order numbers are assigned atomically without race conditions or duplicates.
3. No orders exceed the configured timeslot capacity.
4. Uses or registers a product without stock limits (stock: null) so rejections are solely due to slot capacity.
"""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BOGOTA_TZ = timezone(timedelta(hours=-5))


def get_now_bogota() -> datetime:
    """Return current time in America/Bogota timezone."""
    return datetime.now(BOGOTA_TZ)


def http_request(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 10.0,
) -> Tuple[int, Any]:
    """Execute HTTP request using standard library urllib."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    body_bytes = json.dumps(data).encode("utf-8") if data is not None else None

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content = resp.read().decode("utf-8")
            try:
                body = json.loads(content)
            except Exception:
                body = content
            return status, body
    except urllib.error.HTTPError as e:
        status = e.code
        content = e.read().decode("utf-8")
        try:
            body = json.loads(content)
        except Exception:
            body = content
        return status, body
    except Exception as e:
        return 0, str(e)


def wait_for_services(base_url: str, timeout_seconds: int = 30) -> None:
    """Wait until Nginx and microservices are healthy."""
    print(f"[*] Checking services health at {base_url}...")
    start = time.time()
    while time.time() - start < timeout_seconds:
        status_menu, _ = http_request(f"{base_url}/api/menu/health", timeout=3.0)
        status_orders, _ = http_request(f"{base_url}/api/orders/health", timeout=3.0)
        if status_menu == 200 and status_orders == 200:
            print("    [+] All microservices are healthy and reachable.")
            return
        time.sleep(1.5)
    print("    [!] Warning: Microservices healthcheck timed out. Proceeding anyway...")


def ensure_product_without_stock(base_url: str) -> int:
    """Find or create a product with stock=null (no stock control)."""
    status, menu = http_request(f"{base_url}/api/menu/menu")
    if status == 200 and isinstance(menu, list):
        for prod in menu:
            # Check for available product with no stock control
            if prod.get("status") == "AVAILABLE" and prod.get("stock") is None:
                print(f"    [+] Found existing product with no stock control: '{prod['name']}' (ID: {prod['id']})")
                return prod["id"]

    # Register a new product without stock limits (stock defaults to null in DB)
    product_name = f"Plato Concurrencia {int(time.time()) % 10000}"
    create_payload = {
        "name": product_name,
        "category": "lunch",
        "price": 12500,
    }
    status, created = http_request(f"{base_url}/api/menu/products", method="POST", data=create_payload)
    if status == 201 and isinstance(created, dict) and "id" in created:
        print(f"    [+] Registered new test product with stock=null: '{product_name}' (ID: {created['id']})")
        return created["id"]

    raise RuntimeError(f"Failed to create or find test product. Response: {status} {created}")


def prepare_timeslot(base_url: str, desired_capacity: int = 10) -> str:
    """Find or configure an available timeslot with 0 existing orders and desired capacity."""
    # Ensure slot capacity is set to desired_capacity
    status, config = http_request(f"{base_url}/api/orders/config", method="GET")
    start_time = "11:30"
    end_time = "14:00"
    if status == 200 and isinstance(config, dict):
        start_time = config.get("start_time", "11:30")
        end_time = config.get("end_time", "14:00")

    # Update slot_capacity to desired_capacity (e.g. 10)
    http_request(
        f"{base_url}/api/orders/config",
        method="PUT",
        data={"slot_capacity": desired_capacity, "start_time": start_time, "end_time": end_time},
    )

    # Check current slots
    status, slots = http_request(f"{base_url}/api/orders/slots")
    if status == 200 and isinstance(slots, list):
        for s in slots:
            if s.get("status") == "available" and s.get("current_orders", 0) == 0:
                print(f"    [+] Selected existing clean timeslot: {s['slot']} (Capacity: {s.get('capacity')})")
                return s["slot"]

    # If no clean slot is available (e.g. out of hours or all used), adjust schedule to include a valid future slot today
    now = get_now_bogota()
    # At least 20 minutes from now, rounded up to next 15-minute boundary
    rem = (15 - (now.minute % 15)) % 15
    lead = 20 + rem
    future_start = now + timedelta(minutes=lead)
    future_end = future_start + timedelta(minutes=45)

    new_start_str = future_start.strftime("%H:%M")
    new_end_str = future_end.strftime("%H:%M")

    print(f"    [*] Adjusting schedule to future window: {new_start_str} - {new_end_str} with capacity {desired_capacity}...")
    status, _ = http_request(
        f"{base_url}/api/orders/config",
        method="PUT",
        data={
            "slot_capacity": desired_capacity,
            "start_time": new_start_str,
            "end_time": new_end_str,
        },
    )

    # Re-fetch slots
    status, slots = http_request(f"{base_url}/api/orders/slots")
    if status == 200 and isinstance(slots, list):
        for s in slots:
            if s.get("status") == "available" and s.get("current_orders", 0) == 0:
                print(f"    [+] Selected timeslot after schedule adjustment: {s['slot']}")
                return s["slot"]

    raise RuntimeError("Could not find or configure a valid timeslot for the test.")


def send_order(base_url: str, index: int, product_id: int, slot: str) -> Tuple[int, int, Any]:
    """Send a single order request."""
    payload = {
        "customer_name": f"Student Concurrency {index:02d}",
        "student_code": f"2026{index:04d}",
        "slot": slot,
        "items": [{"product_id": product_id, "quantity": 1}],
    }
    status, body = http_request(f"{base_url}/api/orders/orders", method="POST", data=payload)
    return index, status, body


def run_concurrency_test(base_url: str, total_orders: int = 20, capacity: int = 10) -> bool:
    """Execute the concurrency test with simultaneous requests."""
    print("=" * 70)
    print(" LUNCH ON TIME - CONCURRENCY AND CAPACITY ENFORCEMENT TEST")
    print("=" * 70)
    print(f"Target:       {base_url}")
    print(f"Total Orders: {total_orders}")
    print(f"Timeslot Cap: {capacity}")
    print("-" * 70)

    wait_for_services(base_url)

    print("\n[Step 1] Preparing test product without stock limit (stock=null)...")
    product_id = ensure_product_without_stock(base_url)

    print("\n[Step 2] Selecting timeslot...")
    slot = prepare_timeslot(base_url, desired_capacity=capacity)

    print(f"\n[Step 3] Dispatching {total_orders} simultaneous orders for timeslot '{slot}'...")
    start_time = time.time()

    results: List[Tuple[int, int, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=total_orders) as executor:
        futures = [
            executor.submit(send_order, base_url, i, product_id, slot)
            for i in range(1, total_orders + 1)
        ]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    elapsed = time.time() - start_time
    print(f"    [+] All {total_orders} requests completed in {elapsed:.2f} seconds.")

    # Sort results by client index for readable display
    results.sort(key=lambda x: x[0])

    # Analysis
    accepted_orders: List[Dict[str, Any]] = []
    rejected_orders: List[Tuple[int, int, str]] = []

    for idx, status, body in results:
        if status == 201 and isinstance(body, dict):
            accepted_orders.append(body)
        else:
            detail = body.get("detail", str(body)) if isinstance(body, dict) else str(body)
            rejected_orders.append((idx, status, detail))

    print("\n" + "=" * 70)
    print(" TEST RESULTS & VERIFICATION")
    print("=" * 70)
    print(f"Total Requests:     {total_orders}")
    print(f"Orders Accepted:    {len(accepted_orders)} (Expected: {capacity})")
    print(f"Orders Rejected:    {len(rejected_orders)} (Expected: {total_orders - capacity})")

    assigned_numbers = [o.get("number") for o in accepted_orders]
    print(f"Assigned Numbers:   {assigned_numbers}")

    # Checks
    success = True

    # 1. Check accepted count equals capacity
    if len(accepted_orders) == capacity:
        print("  [PASS] Accepted order count exactly matches timeslot capacity.")
    else:
        print(f"  [FAIL] Expected {capacity} accepted orders, but got {len(accepted_orders)}.")
        success = False

    # 2. Check no duplicate numbers
    if len(assigned_numbers) == len(set(assigned_numbers)):
        print("  [PASS] No duplicate order numbers detected (Atomic sequence verified).")
    else:
        print("  [FAIL] Duplicate order numbers found in accepted orders!")
        success = False

    # 3. Check rejection reasons
    non_capacity_rejections = [
        (idx, code, msg) for idx, code, msg in rejected_orders if "llena" not in msg.lower()
    ]
    if not non_capacity_rejections:
        print("  [PASS] All rejections were due to timeslot capacity exhaustion (409 Conflict).")
    else:
        print(f"  [WARN] Unexpected rejection reasons found: {non_capacity_rejections}")
        success = False

    # 4. Check sequential numbers
    sorted_numbers = sorted(assigned_numbers)
    is_consecutive = all(
        sorted_numbers[i] + 1 == sorted_numbers[i + 1]
        for i in range(len(sorted_numbers) - 1)
    )
    if is_consecutive:
        print("  [PASS] Order numbers form a continuous sequential range.")
    else:
        print("  [INFO] Order numbers are unique but may span across existing sequence numbers.")

    print("-" * 70)
    if success:
        print(" OVERALL RESULT: CONCURRENCY TEST PASSED SUCCESSFULLY!")
    else:
        print(" OVERALL RESULT: CONCURRENCY TEST FAILED - CHECK DETAILS ABOVE.")
    print("=" * 70)
    return success


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run concurrency test against Lunch on Time orders API."
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8080",
        help="Base URL of the Nginx gateway (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--orders",
        type=int,
        default=20,
        help="Number of simultaneous orders to send (default: 20)",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        default=10,
        help="Timeslot capacity to test against (default: 10)",
    )
    args = parser.parse_args()

    success = run_concurrency_test(
        base_url=args.url.rstrip("/"),
        total_orders=args.orders,
        capacity=args.capacity,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
