#!/usr/bin/env python3
"""
Webhook Courier Stress Test

Generates concurrent load to benchmark throughput, latency, and error rates.

Usage:
    python scripts/stress_test.py [--base-url URL] [--endpoints N] [--messages N] [--concurrency N]
"""
import asyncio
import argparse
import json
import time
import statistics
from dataclasses import dataclass, field

import httpx


@dataclass
class StressResult:
    total_sent: int = 0
    total_success: int = 0
    total_error: int = 0
    latencies: list[float] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def throughput(self) -> float:
        return self.total_sent / self.duration if self.duration > 0 else 0

    def report(self):
        print("\n" + "=" * 60)
        print("  STRESS TEST RESULTS")
        print("=" * 60)
        print(f"  Duration:       {self.duration:.2f}s")
        print(f"  Total sent:     {self.total_sent}")
        print(f"  Successful:     {self.total_success}")
        print(f"  Errors:         {self.total_error}")
        print(f"  Throughput:     {self.throughput:.1f} msg/s")
        if self.latencies:
            self.latencies.sort()
            print(f"  Avg latency:    {statistics.mean(self.latencies):.1f}ms")
            print(f"  P50 latency:    {self.latencies[len(self.latencies)//2]:.1f}ms")
            p95_idx = int(len(self.latencies) * 0.95)
            print(f"  P95 latency:    {self.latencies[p95_idx]:.1f}ms")
            p99_idx = int(len(self.latencies) * 0.99)
            print(f"  P99 latency:    {self.latencies[p99_idx]:.1f}ms")
            print(f"  Max latency:    {max(self.latencies):.1f}ms")
        print("=" * 60)


async def create_endpoints(client: httpx.AsyncClient, count: int) -> list[str]:
    endpoint_ids = []
    for i in range(count):
        resp = await client.post("/endpoints", json={
            "url": f"https://httpbin.org/post",
            "secret": f"stresstest_secret_{i:04d}",
            "description": f"Stress test endpoint {i}",
            "rate_limit_per_sec": 100,
            "max_retries": 1,
            "retry_base_interval": 1.0,
        })
        if resp.status_code == 201:
            endpoint_ids.append(resp.json()["id"])
    return endpoint_ids


async def send_messages(
    client: httpx.AsyncClient,
    endpoint_ids: list[str],
    total: int,
    concurrency: int,
    result: StressResult,
):
    sem = asyncio.Semaphore(concurrency)

    async def send_one(idx: int):
        ep_id = endpoint_ids[idx % len(endpoint_ids)]
        payload = json.dumps({"event": "stress.test", "index": idx, "ts": time.time()})
        start = time.perf_counter()
        try:
            async with sem:
                resp = await client.post("/messages", json={
                    "endpoint_id": ep_id,
                    "idempotency_key": f"stress_{idx}_{time.time_ns()}",
                    "payload": payload,
                    "event_type": "stress.test",
                })
            latency = (time.perf_counter() - start) * 1000
            result.latencies.append(latency)
            result.total_sent += 1
            if resp.status_code == 202:
                result.total_success += 1
            else:
                result.total_error += 1
        except Exception:
            result.total_sent += 1
            result.total_error += 1

    tasks = [send_one(i) for i in range(total)]
    await asyncio.gather(*tasks)


async def main():
    parser = argparse.ArgumentParser(description="Webhook Courier Stress Test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--endpoints", type=int, default=5)
    parser.add_argument("--messages", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    args = parser.parse_args()

    print(f"Stress test: {args.messages} messages, {args.concurrency} concurrent, {args.endpoints} endpoints")

    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        health = await client.get("/health")
        if health.status_code != 200:
            print("Service not reachable!")
            return

        print("Creating endpoints...")
        endpoint_ids = await create_endpoints(client, args.endpoints)
        if not endpoint_ids:
            print("Failed to create endpoints!")
            return
        print(f"Created {len(endpoint_ids)} endpoints")

        result = StressResult()
        result.start_time = time.perf_counter()

        print(f"Sending {args.messages} messages...")
        await send_messages(client, endpoint_ids, args.messages, args.concurrency, result)

        result.end_time = time.perf_counter()
        result.report()


if __name__ == "__main__":
    asyncio.run(main())
