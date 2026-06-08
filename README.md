# Webhook Courier

Production-grade webhook delivery core module with at-least-once semantics.

## Features

| Feature | Description |
|---------|-------------|
| Endpoint CRUD | Full lifecycle management with per-endpoint configuration |
| Idempotent Ingestion | Business key dedup prevents duplicate deliveries |
| At-Least-Once Delivery | Crash recovery re-claims in-flight messages from persistent storage |
| Exponential Backoff | Per-endpoint configurable retries: `base_interval × 2^attempt` |
| Dead Letter Queue | Exhausted messages stored for inspection and manual replay |
| Rate Limiting | Token-bucket per endpoint (configurable max deliveries/sec) |
| HMAC Signing | SHA-256 signature + timestamp in delivery headers |
| Structured Logging | JSON to stdout + rotating file (10MB × 5 files) |

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python demo.py
```

## Run as Service

```bash
uvicorn webhook_courier.main:app --host 0.0.0.0 --port 8000
```

## API Endpoints

```
POST   /endpoints              Create endpoint
GET    /endpoints              List endpoints
GET    /endpoints/:id          Get endpoint
PATCH  /endpoints/:id          Update endpoint
DELETE /endpoints/:id          Delete endpoint

POST   /messages               Ingest message (async delivery)
GET    /messages/:id           Get message status

GET    /dlq                    List dead letters
POST   /dlq/:id/replay        Replay dead letter

GET    /health                 Health check
```

## Architecture

```
Ingestion (HTTP)           Persistent Store (SQLite)          Delivery
┌─────────────────┐       ┌───────────────────────┐       ┌──────────────────┐
│ POST /messages  │──────▶│ messages table        │──────▶│ Dispatcher       │
│ • Validate      │       │ • status: PENDING     │       │ • Poll PENDING   │
│ • Dedup by key  │       │ • idempotency index   │       │ • Mark IN_FLIGHT │
│ • Return 202    │       │                       │       │ • HTTP POST      │
└─────────────────┘       │ On crash recovery:    │       │ • Sign payload   │
                          │ IN_FLIGHT → PENDING   │       │ • Rate limit     │
                          └───────────────────────┘       └────────┬─────────┘
                                                                   │
                          ┌───────────────────────┐                │
                          │ dead_letters table    │◀───────────────┘
                          │ • Manual replay       │     (after max retries)
                          └───────────────────────┘
```

## Configuration per Endpoint

| Field | Default | Description |
|-------|---------|-------------|
| `max_retries` | 5 | Maximum delivery attempts before DLQ |
| `retry_base_interval` | 2.0s | Base for exponential backoff |
| `rate_limit_per_sec` | 50 | Token bucket refill rate |

## SQLite Schema

See `init_db.sql` for standalone schema, or let the app auto-create via SQLAlchemy on startup.
