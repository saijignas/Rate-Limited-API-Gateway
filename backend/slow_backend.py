"""A toy 'real' backend the gateway sits in front of -- simulates a
payment/order-style API with artificial latency, and exposes a
process-count so tests and the demo script can *prove* idempotency
prevented double-processing, rather than just asserting it did.

Run standalone with: uvicorn backend.slow_backend:app --port 9000
"""

import itertools
import time

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Slow Backend (toy payment/order service)")

_order_id_counter = itertools.count(1)
_process_count = {"orders_created": 0, "reads": 0}


class CreateOrderRequest(BaseModel):
    item: str
    amount_cents: int


@app.post("/orders")
def create_order(req: CreateOrderRequest):
    time.sleep(0.2)  # simulate real work: payment authorization, DB write
    _process_count["orders_created"] += 1
    order_id = next(_order_id_counter)
    return {
        "order_id": order_id,
        "item": req.item,
        "amount_cents": req.amount_cents,
        "status": "created",
    }


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    time.sleep(0.1)  # simulate real work: a slow read
    _process_count["reads"] += 1
    return {"order_id": order_id, "status": "created"}


@app.get("/_debug/process_count")
def debug_process_count():
    """Not part of the 'real' API -- exists purely so tests/demo can
    verify how many times the backend actually did work."""
    return _process_count
