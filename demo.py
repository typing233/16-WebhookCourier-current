#!/usr/bin/env python3
"""
Webhook Courier Demo
====================
Starts the webhook courier service along with a mock receiver endpoint.
Demonstrates: endpoint creation, message ingestion, dedup, delivery, retry, DLQ, and replay.

Usage:
    pip install -r requirements.txt
    python demo.py
"""
import asyncio
import json
import time
import httpx
import uvicorn
import threading
from fastapi import FastAPI, Request, Response

COURIER_PORT = 8000
RECEIVER_PORT = 9000
BASE = f"http://127.0.0.1:{COURIER_PORT}"

# --- Mock Receiver ---
receiver_app = FastAPI()
received_messages: list[dict] = []
fail_count = 0
MAX_INITIAL_FAILURES = 3


@receiver_app.post("/webhook/ok")
async def webhook_ok(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Webhook-Signature", "")
    received_messages.append({"body": body.decode(), "signature": sig})
    print(f"  [Receiver /ok] Got message #{len(received_messages)}: {body.decode()[:80]}")
    return {"status": "received"}


@receiver_app.post("/webhook/flaky")
async def webhook_flaky(request: Request):
    """Fails the first MAX_INITIAL_FAILURES times, then succeeds."""
    global fail_count
    fail_count += 1
    if fail_count <= MAX_INITIAL_FAILURES:
        print(f"  [Receiver /flaky] Failing attempt {fail_count}/{MAX_INITIAL_FAILURES}")
        return Response(status_code=503, content="Service Unavailable")
    body = await request.body()
    print(f"  [Receiver /flaky] Success after {MAX_INITIAL_FAILURES} failures")
    return {"status": "received"}


@receiver_app.post("/webhook/dead")
async def webhook_dead(request: Request):
    """Always fails - messages will end up in DLQ."""
    return Response(status_code=500, content="Always fails")


def run_receiver():
    uvicorn.run(receiver_app, host="127.0.0.1", port=RECEIVER_PORT, log_level="warning")


# --- Demo Logic ---
async def run_demo():
    print("\n" + "=" * 60)
    print("  WEBHOOK COURIER DEMO")
    print("=" * 60)

    await asyncio.sleep(2)
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as client:

        # 1. Create endpoints
        print("\n[1] Creating endpoints...")
        ep_ok = (await client.post("/endpoints", json={
            "url": f"http://127.0.0.1:{RECEIVER_PORT}/webhook/ok",
            "secret": "supersecretkey123456",
            "description": "Reliable endpoint",
            "rate_limit_per_sec": 10,
        })).json()
        print(f"    Created: {ep_ok['id'][:8]}... (reliable)")

        ep_flaky = (await client.post("/endpoints", json={
            "url": f"http://127.0.0.1:{RECEIVER_PORT}/webhook/flaky",
            "secret": "anothersecretkey1234",
            "description": "Flaky endpoint (retries needed)",
            "max_retries": 5,
            "retry_base_interval": 1.0,
            "rate_limit_per_sec": 5,
        })).json()
        print(f"    Created: {ep_flaky['id'][:8]}... (flaky)")

        ep_dead = (await client.post("/endpoints", json={
            "url": f"http://127.0.0.1:{RECEIVER_PORT}/webhook/dead",
            "secret": "deadsecretkey1234567",
            "description": "Always-failing endpoint (DLQ test)",
            "max_retries": 2,
            "retry_base_interval": 1.0,
        })).json()
        print(f"    Created: {ep_dead['id'][:8]}... (always fails)")

        # 2. Ingest messages
        print("\n[2] Ingesting messages...")
        payload = json.dumps({"event": "order.created", "order_id": "ORD-001"})

        msg1 = (await client.post("/messages", json={
            "endpoint_id": ep_ok["id"],
            "idempotency_key": "order-001-created",
            "payload": payload,
        })).json()
        print(f"    Message {msg1['id'][:8]}... -> reliable endpoint")

        # 3. Idempotent dedup test
        print("\n[3] Testing idempotent deduplication...")
        msg1_dup = (await client.post("/messages", json={
            "endpoint_id": ep_ok["id"],
            "idempotency_key": "order-001-created",
            "payload": payload,
        })).json()
        assert msg1["id"] == msg1_dup["id"], "Dedup failed!"
        print(f"    Same idempotency key returned same ID: {msg1['id'][:8]}... ✓")

        # 4. Message to flaky endpoint (will retry)
        print("\n[4] Sending to flaky endpoint (will fail 3x then succeed)...")
        msg2 = (await client.post("/messages", json={
            "endpoint_id": ep_flaky["id"],
            "idempotency_key": "flaky-test-001",
            "payload": json.dumps({"event": "payment.processed", "amount": 99.99}),
        })).json()
        print(f"    Message {msg2['id'][:8]}... -> flaky endpoint")

        # 5. Message to dead endpoint (will exhaust retries -> DLQ)
        print("\n[5] Sending to always-failing endpoint (will go to DLQ)...")
        msg3 = (await client.post("/messages", json={
            "endpoint_id": ep_dead["id"],
            "idempotency_key": "dead-test-001",
            "payload": json.dumps({"event": "user.deleted", "user_id": "USR-999"}),
        })).json()
        print(f"    Message {msg3['id'][:8]}... -> dead endpoint")

        # Wait for delivery attempts
        print("\n[6] Waiting for delivery cycles...")
        for i in range(15):
            await asyncio.sleep(2)
            status = (await client.get(f"/messages/{msg3['id']}")).json()
            if status["status"] == "dead":
                print(f"    Dead letter reached after {i*2}s")
                break
            print(f"    ...{i*2}s elapsed, msg3 status: {status['status']} (attempts: {status['attempt_count']})")

        # 6. Check DLQ
        print("\n[7] Checking dead letter queue...")
        dlq = (await client.get("/dlq")).json()
        print(f"    Dead letters: {len(dlq)}")
        for dl in dlq:
            print(f"      - {dl['id'][:8]}... | msg: {dl['message_id'][:8]}... | error: {dl['last_error']}")

        # 7. Replay from DLQ
        if dlq:
            print("\n[8] Replaying first dead letter...")
            replay_resp = (await client.post(f"/dlq/{dlq[0]['id']}/replay")).json()
            print(f"    New message created: {replay_resp['new_message_id'][:8]}...")

        # 8. Verify delivery
        print("\n[9] Checking message statuses...")
        for label, mid in [("reliable", msg1["id"]), ("flaky", msg2["id"]), ("dead", msg3["id"])]:
            st = (await client.get(f"/messages/{mid}")).json()
            print(f"    {label}: status={st['status']}, attempts={st['attempt_count']}")

        print(f"\n[10] Total messages received by /ok endpoint: {len(received_messages)}")

    print("\n" + "=" * 60)
    print("  DEMO COMPLETE")
    print("=" * 60 + "\n")


def run_courier():
    uvicorn.run("webhook_courier.main:app", host="127.0.0.1", port=COURIER_PORT, log_level="info")


if __name__ == "__main__":
    # Start receiver in background thread
    t_receiver = threading.Thread(target=run_receiver, daemon=True)
    t_receiver.start()

    # Start courier in background thread
    t_courier = threading.Thread(target=run_courier, daemon=True)
    t_courier.start()

    # Run demo
    asyncio.run(run_demo())
