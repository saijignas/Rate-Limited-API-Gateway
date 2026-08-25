import fakeredis
import pytest

from gateway.idempotency import IdempotencyConflict, IdempotencyStore


@pytest.fixture
def store():
    return IdempotencyStore(fakeredis.FakeRedis())


def test_first_call_with_new_key_is_new(store):
    status, cached = store.begin("key-1")
    assert status == "new"
    assert cached is None


def test_concurrent_second_call_while_pending_raises_conflict(store):
    store.begin("key-1")  # first caller claims it, starts "processing"
    with pytest.raises(IdempotencyConflict):
        store.begin("key-1")  # second caller arrives before completion


def test_call_after_completion_returns_cached_response(store):
    store.begin("key-1")
    store.complete("key-1", status_code=201, body={"order_id": 42})

    status, cached = store.begin("key-1")
    assert status == "completed"
    assert cached["status_code"] == 201
    assert cached["body"] == {"order_id": 42}


def test_release_allows_retry_after_failed_processing(store):
    store.begin("key-1")
    # Simulate the handler raising an exception before calling complete().
    store.release("key-1")

    status, cached = store.begin("key-1")
    assert status == "new"
    assert cached is None


def test_different_keys_are_independent(store):
    store.begin("key-1")
    store.complete("key-1", status_code=201, body={"order_id": 1})

    status, cached = store.begin("key-2")
    assert status == "new"
    assert cached is None


def test_release_on_already_completed_key_is_a_no_op(store):
    store.begin("key-1")
    store.complete("key-1", status_code=201, body={"order_id": 1})
    store.release("key-1")  # must not delete a completed record

    status, cached = store.begin("key-1")
    assert status == "completed"
    assert cached["body"] == {"order_id": 1}
