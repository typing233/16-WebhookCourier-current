"""
Database migration script: v1 -> v2

Usage: python -m webhook_courier.migrations.migrate_v2

Adds new tables and columns for v2 features:
- Multi-tenancy (applications, api_keys)
- Delivery logs
- Subscriptions / event routing
- Event schemas
- Alert configs
- Circuit breaker state on endpoints

Idempotent: safe to run multiple times.
"""
import os
import sys
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///webhook_courier.db")


def get_db_path() -> str:
    if DATABASE_URL.startswith("sqlite:///"):
        return DATABASE_URL.replace("sqlite:///", "")
    return "webhook_courier.db"


def column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    return column in columns


def table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def migrate():
    db_path = get_db_path()
    if not Path(db_path).exists():
        print(f"Database {db_path} does not exist. It will be created on app startup.")
        return

    backup_path = f"{db_path}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(db_path, backup_path)
    print(f"Backup created: {backup_path}")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Schema version tracking
        if not table_exists(cursor, "schema_version"):
            cursor.execute("""
                CREATE TABLE schema_version (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)
            print("Created table: schema_version")

        # Applications
        if not table_exists(cursor, "applications"):
            cursor.execute("""
                CREATE TABLE applications (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    signing_key TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)
            print("Created table: applications")

        # API Keys
        if not table_exists(cursor, "api_keys"):
            cursor.execute("""
                CREATE TABLE api_keys (
                    id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL REFERENCES applications(id),
                    key_hash TEXT NOT NULL UNIQUE,
                    prefix TEXT NOT NULL,
                    label TEXT DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_api_keys_app_id ON api_keys(app_id)")
            print("Created table: api_keys")

        # Delivery Logs
        if not table_exists(cursor, "delivery_logs"):
            cursor.execute("""
                CREATE TABLE delivery_logs (
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
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_delivery_logs_message_id ON delivery_logs(message_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_delivery_logs_endpoint_created ON delivery_logs(endpoint_id, created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_delivery_logs_app_created ON delivery_logs(app_id, created_at)")
            print("Created table: delivery_logs")

        # Subscriptions
        if not table_exists(cursor, "subscriptions"):
            cursor.execute("""
                CREATE TABLE subscriptions (
                    id TEXT PRIMARY KEY,
                    app_id TEXT REFERENCES applications(id),
                    endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
                    event_type TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(endpoint_id, event_type)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_subscriptions_app_id ON subscriptions(app_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_subscriptions_endpoint_id ON subscriptions(endpoint_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_subscriptions_event_type ON subscriptions(event_type)")
            print("Created table: subscriptions")

        # Event Schemas
        if not table_exists(cursor, "event_schemas"):
            cursor.execute("""
                CREATE TABLE event_schemas (
                    id TEXT PRIMARY KEY,
                    app_id TEXT REFERENCES applications(id),
                    event_type TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    schema_json TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    UNIQUE(app_id, event_type, version)
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_event_schemas_app_id ON event_schemas(app_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_event_schemas_event_type ON event_schemas(event_type)")
            print("Created table: event_schemas")

        # Alert Configs
        if not table_exists(cursor, "alert_configs"):
            cursor.execute("""
                CREATE TABLE alert_configs (
                    id TEXT PRIMARY KEY,
                    app_id TEXT REFERENCES applications(id),
                    endpoint_id TEXT REFERENCES endpoints(id),
                    channel TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    failure_threshold INTEGER NOT NULL DEFAULT 3,
                    cooldown_seconds INTEGER NOT NULL DEFAULT 300,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_alert_configs_app_id ON alert_configs(app_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_alert_configs_endpoint_id ON alert_configs(endpoint_id)")
            print("Created table: alert_configs")

        # Alert Logs
        if not table_exists(cursor, "alert_logs"):
            cursor.execute("""
                CREATE TABLE alert_logs (
                    id TEXT PRIMARY KEY,
                    config_id TEXT NOT NULL REFERENCES alert_configs(id),
                    endpoint_id TEXT NOT NULL,
                    error_fingerprint TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_alert_logs_config_id ON alert_logs(config_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS ix_alert_logs_endpoint_id ON alert_logs(endpoint_id)")
            print("Created table: alert_logs")

        # --- ALTER existing tables ---

        # Endpoints: new columns
        alter_columns = [
            ("endpoints", "app_id", "TEXT"),
            ("endpoints", "jitter_strategy", "TEXT DEFAULT 'full'"),
            ("endpoints", "per_attempt_timeout", "REAL DEFAULT 10.0"),
            ("endpoints", "max_backoff", "REAL DEFAULT 3600.0"),
            ("endpoints", "health_check_url", "TEXT"),
            ("endpoints", "health_check_method", "TEXT DEFAULT 'HEAD'"),
            ("endpoints", "health_check_interval", "INTEGER"),
            ("endpoints", "circuit_state", "TEXT DEFAULT 'CLOSED'"),
            ("endpoints", "circuit_opened_at", "TEXT"),
            ("endpoints", "failure_count", "INTEGER DEFAULT 0"),
            ("endpoints", "success_count", "INTEGER DEFAULT 0"),
        ]
        for table, col, col_type in alter_columns:
            if not column_exists(cursor, table, col):
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                print(f"  Added column: {table}.{col}")

        # Messages: new columns
        msg_columns = [
            ("messages", "app_id", "TEXT"),
            ("messages", "event_type", "TEXT"),
        ]
        for table, col, col_type in msg_columns:
            if not column_exists(cursor, table, col):
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                print(f"  Added column: {table}.{col}")

        # Dead letters: new columns
        dl_columns = [
            ("dead_letters", "app_id", "TEXT"),
            ("dead_letters", "event_type", "TEXT"),
        ]
        for table, col, col_type in dl_columns:
            if not column_exists(cursor, table, col):
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                print(f"  Added column: {table}.{col}")

        # Record migration
        cursor.execute("INSERT INTO schema_version (version) VALUES (?)", ("2.0.0",))

        conn.commit()
        print("\nMigration to v2.0.0 completed successfully.")

    except Exception as e:
        conn.rollback()
        print(f"\nMigration failed: {e}")
        print(f"Database restored from backup: {backup_path}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
