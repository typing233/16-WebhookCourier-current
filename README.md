# Webhook Courier

Production-grade webhook delivery system with at-least-once semantics, multi-tenancy, and full lifecycle management.

## Features

| Feature | Description |
|---------|-------------|
| **Endpoint CRUD** | Full lifecycle management with per-endpoint configuration |
| **Idempotent Ingestion** | Business key dedup prevents duplicate deliveries |
| **At-Least-Once Delivery** | Crash recovery re-claims in-flight messages from persistent storage |
| **Exponential Backoff + Jitter** | Configurable strategies: none, full, equal, decorrelated |
| **Circuit Breaker** | Per-endpoint CLOSED/OPEN/HALF_OPEN with auto-recovery |
| **Health Checks** | Periodic HEAD/GET probes with auto-disable/enable |
| **Dead Letter Queue** | Exhausted messages stored with batch replay and purge |
| **Event Routing** | Route messages by event_type to subscribed endpoints |
| **Schema Validation** | Versioned JSON Schema validation at ingest time |
| **Multi-Tenancy** | Application isolation with independent API keys and signing |
| **Rate Limiting** | Per-endpoint + global token bucket rate limiter |
| **Delivery Logs** | Per-attempt audit trail with stats, filtering, and CSV/JSON export |
| **Failure Alerts** | Webhook/email alerts with dedup and noise reduction |
| **Prometheus Metrics** | `/metrics` endpoint with counters, gauges, and histograms |
| **HMAC Signing** | SHA-256 signature + timestamp in delivery headers |
| **Structured Logging** | JSON to stdout + rotating file (10MB × 5 files) |
| **Hot Config Reload** | Runtime config update via API or file watcher |

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

## Database Migration (v1 → v2)

For existing deployments, run the migration script before upgrading:

```bash
python -m webhook_courier.migrations.migrate_v2
```

This adds new tables and columns non-destructively. A backup is created automatically.

## Configuration

All settings support environment variables and an optional `config.json` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///webhook_courier.db` | Database connection URL |
| `AUTH_ENABLED` | `false` | Enable API key authentication |
| `DISPATCHER_CONCURRENCY` | `10` | Max concurrent delivery coroutines |
| `DISPATCHER_POLL_INTERVAL` | `1.0` | Seconds between poll cycles |
| `DISPATCHER_BATCH_SIZE` | `100` | Messages fetched per poll |
| `DELIVERY_TIMEOUT` | `10.0` | Default HTTP timeout (seconds) |
| `GLOBAL_RATE_LIMIT_PER_SEC` | `1000` | Global throughput cap |
| `DEFAULT_RETRY_JITTER` | `full` | Jitter strategy: none/full/equal/decorrelated |
| `DEFAULT_MAX_RETRIES` | `5` | Default max delivery attempts |
| `DEFAULT_RETRY_BASE_INTERVAL` | `2.0` | Default backoff base (seconds) |
| `DEFAULT_MAX_BACKOFF` | `3600.0` | Maximum backoff delay (seconds) |
| `HEALTH_CHECK_INTERVAL` | `60` | Seconds between health probes |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive failures to trip |
| `CIRCUIT_BREAKER_RECOVERY_TIMEOUT` | `30` | Seconds in OPEN before HALF_OPEN |
| `ALERT_WEBHOOK_URL` | | Global alert webhook URL |
| `ALERT_EMAIL_SMTP_HOST` | | SMTP host for email alerts |
| `ALERT_RATE_LIMIT_WINDOW` | `300` | Alert dedup window (seconds) |

Hot-reload: `POST /admin/reload-config`

## API Endpoints

### Core Delivery
```
POST   /messages               Ingest message (async delivery, 202)
POST   /messages/route         Route by event_type to subscribed endpoints
GET    /messages               List messages (filters: endpoint_id, status, event_type, dates)
GET    /messages/:id           Get message status
```

### Endpoint Management
```
POST   /endpoints              Create endpoint
GET    /endpoints              List endpoints (filter: is_active)
GET    /endpoints/:id          Get endpoint (includes circuit breaker state)
PATCH  /endpoints/:id          Update endpoint
DELETE /endpoints/:id          Delete endpoint
```

### Dead Letter Queue
```
GET    /dlq                    List dead letters (rich filters)
GET    /dlq/stats              DLQ statistics
POST   /dlq/:id/replay        Replay single dead letter
POST   /dlq/batch-replay      Batch replay (by IDs or filter)
POST   /dlq/purge             Purge dead letters
```

### Applications & Auth
```
POST   /applications           Create application
GET    /applications           List applications
GET    /applications/:id       Get application
PATCH  /applications/:id       Update application
DELETE /applications/:id       Delete application
POST   /applications/:id/keys  Create API key
GET    /applications/:id/keys  List API keys
DELETE /applications/:id/keys/:key_id  Revoke API key
```

### Event Routing
```
POST   /subscriptions          Subscribe endpoint to event_type
GET    /subscriptions          List subscriptions
GET    /subscriptions/:id      Get subscription
DELETE /subscriptions/:id      Delete subscription
```

### Schema Validation
```
POST   /schemas                Register versioned JSON Schema
GET    /schemas                List schemas
GET    /schemas/:id            Get schema
DELETE /schemas/:id            Deactivate schema
```

### Alerts
```
POST   /alert-configs          Create alert configuration
GET    /alert-configs          List alert configs
GET    /alert-configs/:id      Get alert config
PATCH  /alert-configs/:id      Update alert config
DELETE /alert-configs/:id      Delete alert config
```

### Delivery Logs
```
GET    /delivery-logs          List delivery attempts (rich filters)
GET    /delivery-logs/stats    Statistics (success rate, latency percentiles)
GET    /delivery-logs/export   Export as CSV or JSON
```

### Operations
```
GET    /health                 Health check
GET    /metrics                Prometheus metrics
POST   /admin/reload-config   Hot-reload configuration
```

## Architecture

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│ API Layer   │    │ Persistent Store │    │ Delivery Engine     │
│             │    │                  │    │                     │
│ /messages   │───▶│ messages         │───▶│ Dispatcher          │
│ /route      │    │ endpoints        │    │ • Concurrent pool   │
│ /endpoints  │    │ subscriptions    │    │ • Circuit breaker   │
│ /dlq        │    │ delivery_logs    │    │ • Rate limiter      │
│ /apps       │    │ dead_letters     │    │ • Retry + jitter    │
└─────────────┘    └──────────────────┘    │ • HMAC signing      │
                                           │ • Delivery logging  │
┌─────────────┐    ┌──────────────────┐    │ • Alert triggers    │
│ Auth Layer  │    │ Background Tasks │    └─────────────────────┘
│             │    │                  │
│ API Keys    │    │ Health Checker   │
│ App Scoping │    │ Config Watcher   │
└─────────────┘    └──────────────────┘
```

## Multi-Tenancy

Enable with `AUTH_ENABLED=true`. Each application gets:
- Independent API keys (Bearer token auth)
- Isolated endpoints, messages, and DLQ
- Separate signing keys
- Scoped delivery logs and subscriptions

When `AUTH_ENABLED=false` (default), the system runs without authentication for backward compatibility.

## Retry Strategies

| Strategy | Formula | Best For |
|----------|---------|----------|
| `none` | `base × 2^attempt` | Predictable retry timing |
| `full` | `random(0, base × 2^attempt)` | General use (recommended) |
| `equal` | `base×2^attempt/2 + random(0, base×2^attempt/2)` | Bounded minimum wait |
| `decorrelated` | `random(base, last_delay × 3)` | Low correlation between clients |

Configure per-endpoint via `jitter_strategy`, `retry_base_interval`, `max_backoff`, and `max_retries`.

## Testing

```bash
pytest tests/ -v
pytest tests/ --cov=webhook_courier --cov-report=term-missing
```

## Stress Testing

```bash
# Start the service first
uvicorn webhook_courier.main:app --port 8000

# Run stress test
python scripts/stress_test.py --messages 5000 --concurrency 100
```

## Project Structure

```
webhook_courier/
├── main.py                  # FastAPI app + lifespan
├── config.py                # Central configuration + hot-reload
├── models.py                # SQLAlchemy models (11 tables)
├── schemas.py               # Pydantic request/response schemas
├── database.py              # Engine + session factory
├── logging_config.py        # JSON structured logging
├── api/
│   ├── endpoints.py         # Endpoint CRUD
│   ├── messages.py          # Message ingest + routing + list
│   ├── applications.py      # Application + API key management
│   ├── delivery_logs.py     # Logs, stats, export
│   ├── subscriptions.py     # Event routing subscriptions
│   ├── schemas.py           # Schema validation CRUD
│   └── alerts.py            # Alert configuration CRUD
├── core/
│   ├── dispatcher.py        # Delivery engine (concurrent, circuit-aware)
│   ├── retry.py             # Backoff calculation with jitter
│   ├── circuit_breaker.py   # Per-endpoint state machine
│   ├── health_checker.py    # Periodic endpoint probing
│   ├── rate_limiter.py      # Token bucket (global + per-endpoint)
│   ├── signer.py            # HMAC-SHA256 signing
│   ├── alerter.py           # Failure alerts with noise reduction
│   └── schema_validator.py  # JSON Schema validation
├── dlq/
│   └── dead_letter.py       # DLQ list, replay, batch, purge
├── auth/
│   └── dependencies.py      # API key auth + app resolution
├── metrics/
│   └── collector.py         # Prometheus-compatible metrics
├── migrations/
│   └── migrate_v2.py        # v1→v2 migration script
scripts/
└── stress_test.py           # Load generation benchmark
tests/
├── conftest.py              # Fixtures (in-memory DB, TestClient)
├── test_endpoints.py
├── test_messages.py
├── test_retry.py
├── test_circuit_breaker.py
├── test_dlq.py
├── test_auth.py
├── test_delivery_logs.py
├── test_subscriptions.py
├── test_schemas.py
├── test_alerter.py
└── test_metrics.py
```
