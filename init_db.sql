-- Webhook Courier SQLite Schema v2.0
-- Run: sqlite3 webhook_courier.db < init_db.sql

-- === Core Tables ===

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    signing_key TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL REFERENCES applications(id),
    key_hash TEXT NOT NULL UNIQUE,
    prefix TEXT NOT NULL,
    label TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_api_keys_app_id ON api_keys(app_id);

CREATE TABLE IF NOT EXISTS endpoints (
    id TEXT PRIMARY KEY,
    app_id TEXT REFERENCES applications(id),
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    description TEXT DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    max_retries INTEGER NOT NULL DEFAULT 5,
    retry_base_interval REAL NOT NULL DEFAULT 2.0,
    jitter_strategy TEXT NOT NULL DEFAULT 'full',
    per_attempt_timeout REAL NOT NULL DEFAULT 10.0,
    max_backoff REAL NOT NULL DEFAULT 3600.0,
    rate_limit_per_sec INTEGER NOT NULL DEFAULT 50,
    health_check_url TEXT,
    health_check_method TEXT NOT NULL DEFAULT 'HEAD',
    health_check_interval INTEGER,
    circuit_state TEXT NOT NULL DEFAULT 'CLOSED',
    circuit_opened_at TEXT,
    failure_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_endpoints_app_id ON endpoints(app_id);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    endpoint_id TEXT NOT NULL,
    app_id TEXT REFERENCES applications(id),
    event_type TEXT,
    idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_response_code INTEGER,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_messages_idempotency ON messages(endpoint_id, idempotency_key);
CREATE INDEX IF NOT EXISTS ix_messages_status_next_attempt ON messages(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_messages_endpoint_id ON messages(endpoint_id);
CREATE INDEX IF NOT EXISTS ix_messages_app_id ON messages(app_id);
CREATE INDEX IF NOT EXISTS ix_messages_event_type ON messages(event_type);

CREATE TABLE IF NOT EXISTS dead_letters (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    endpoint_id TEXT NOT NULL,
    app_id TEXT REFERENCES applications(id),
    event_type TEXT,
    idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_response_code INTEGER,
    last_error TEXT,
    replayed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_dead_letters_message_id ON dead_letters(message_id);
CREATE INDEX IF NOT EXISTS ix_dead_letters_endpoint_id ON dead_letters(endpoint_id);
CREATE INDEX IF NOT EXISTS ix_dead_letters_app_id ON dead_letters(app_id);

-- === Delivery Logs ===

CREATE TABLE IF NOT EXISTS delivery_logs (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    endpoint_id TEXT NOT NULL,
    app_id TEXT,
    attempt_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    response_code INTEGER,
    error_message TEXT,
    latency_ms REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_delivery_logs_message_id ON delivery_logs(message_id);
CREATE INDEX IF NOT EXISTS ix_delivery_logs_endpoint_created ON delivery_logs(endpoint_id, created_at);
CREATE INDEX IF NOT EXISTS ix_delivery_logs_app_created ON delivery_logs(app_id, created_at);

-- === Event Routing ===

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    app_id TEXT REFERENCES applications(id),
    endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
    event_type TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(endpoint_id, event_type)
);
CREATE INDEX IF NOT EXISTS ix_subscriptions_app_id ON subscriptions(app_id);
CREATE INDEX IF NOT EXISTS ix_subscriptions_endpoint_id ON subscriptions(endpoint_id);
CREATE INDEX IF NOT EXISTS ix_subscriptions_event_type ON subscriptions(event_type);

-- === Schema Validation ===

CREATE TABLE IF NOT EXISTS event_schemas (
    id TEXT PRIMARY KEY,
    app_id TEXT REFERENCES applications(id),
    event_type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    schema_json TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(app_id, event_type, version)
);
CREATE INDEX IF NOT EXISTS ix_event_schemas_app_id ON event_schemas(app_id);
CREATE INDEX IF NOT EXISTS ix_event_schemas_event_type ON event_schemas(event_type);

-- === Alerts ===

CREATE TABLE IF NOT EXISTS alert_configs (
    id TEXT PRIMARY KEY,
    app_id TEXT REFERENCES applications(id),
    endpoint_id TEXT REFERENCES endpoints(id),
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    failure_threshold INTEGER NOT NULL DEFAULT 3,
    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_alert_configs_app_id ON alert_configs(app_id);
CREATE INDEX IF NOT EXISTS ix_alert_configs_endpoint_id ON alert_configs(endpoint_id);

CREATE TABLE IF NOT EXISTS alert_logs (
    id TEXT PRIMARY KEY,
    config_id TEXT NOT NULL REFERENCES alert_configs(id),
    endpoint_id TEXT NOT NULL,
    error_fingerprint TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS ix_alert_logs_config_id ON alert_logs(config_id);
CREATE INDEX IF NOT EXISTS ix_alert_logs_endpoint_id ON alert_logs(endpoint_id);

-- === Schema Version ===

CREATE TABLE IF NOT EXISTS schema_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
INSERT INTO schema_version (version) VALUES ('2.0.0');
