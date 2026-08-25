"""Fires real concurrent load against a running gateway (docker-compose up)
and prints measured evidence -- not a claim -- that idempotency and rate
limiting both work under real HTTP + real concurrency, matching this
project's house style: show the numbers, don't just assert behavior.

Usage:
    docker-compose up -d
    python scripts/demo_load_test.py
"""

import concurrent.futures
import json
import time
import uuid

import httpx

GATEWAY_URL = "http://localhost:8080"


def demo_idempotency():
    print("=== Idempotency: 20 concurrent POSTs, same Idempotency-Key ===")
    key = str(uuid.uuid4())
    body = {"item": "demo-widget", "amount_cents": 1500}

    def fire(_):
        with httpx.Client(base_url=GATEWAY_URL, timeout=10.0) as client:
            return client.post("/orders", json=body, headers={"Idempotency-Key": key})

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(fire, range(20)))

    statuses = [r.status_code for r in responses]
    order_ids = {r.json().get("order_id") for r in responses if r.status_code == 200}
    print(f"  20 requests fired, status codes: {sorted(set(statuses))}")
    print(f"  distinct order_ids returned: {order_ids} (should be exactly 1)")
    print(f"  PASS: backend processed the order exactly once\n" if len(order_ids) == 1 else "  FAIL\n")


def demo_rate_limiting():
    print("=== Rate limiting: 30 requests as fast as possible, single client ===")
    with httpx.Client(base_url=GATEWAY_URL, timeout=10.0) as client:
        results = []
        start = time.perf_counter()
        for i in range(30):
            key = str(uuid.uuid4())
            r = client.post(
                "/orders",
                json={"item": f"burst-{i}", "amount_cents": 1},
                headers={"Idempotency-Key": key, "X-Client-Id": "demo-burst-client"},
            )
            results.append(r.status_code)
        elapsed = time.perf_counter() - start

    allowed = results.count(200)
    limited = results.count(429)
    print(f"  {len(results)} requests in {elapsed:.2f}s: {allowed} allowed (200), {limited} rate-limited (429)")
    print(f"  status codes seen: {sorted(set(results))}\n")


def demo_caching():
    print("=== Caching: 5 GETs for the same order ===")
    create_key = str(uuid.uuid4())
    with httpx.Client(base_url=GATEWAY_URL, timeout=10.0) as client:
        created = client.post(
            "/orders",
            json={"item": "cache-demo", "amount_cents": 250},
            headers={"Idempotency-Key": create_key},
        ).json()
        order_id = created["order_id"]

        cache_flags = []
        for _ in range(5):
            r = client.get(f"/orders/{order_id}")
            cache_flags.append(r.json().get("_cache"))

    print(f"  cache flags across 5 identical GETs: {cache_flags}")
    print("  PASS: first miss, rest hits\n" if cache_flags == ["miss"] + ["hit"] * 4 else "  unexpected pattern\n")


if __name__ == "__main__":
    print(f"Target gateway: {GATEWAY_URL}\n")
    demo_idempotency()
    demo_rate_limiting()
    demo_caching()
