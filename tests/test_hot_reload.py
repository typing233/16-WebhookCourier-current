import asyncio
import os
import json
import tempfile
from unittest.mock import patch

import pytest

from webhook_courier.config import Settings
from webhook_courier.core.dispatcher import dispatcher
from webhook_courier.core.rate_limiter import rate_limiter
from webhook_courier.core.health_checker import health_checker


def test_reload_updates_dispatcher_concurrency():
    """Dispatcher semaphore should be replaced on config reload."""
    original = dispatcher._semaphore
    dispatcher._semaphore = asyncio.Semaphore(10)

    dispatcher.update_concurrency(25)
    assert dispatcher._semaphore._value == 25

    dispatcher._semaphore = original


def test_reload_updates_rate_limiter_global_rate():
    """Global rate limit should change on reload."""
    old_rate = rate_limiter._global_rate
    rate_limiter.update_global_rate(500)
    assert rate_limiter._global_rate == 500
    rate_limiter.update_global_rate(old_rate)


def test_reload_updates_health_checker_interval():
    """Health checker interval should change on reload."""
    old_interval = health_checker.interval
    health_checker.interval = 120
    assert health_checker.interval == 120
    health_checker.interval = old_interval


def test_settings_reload_triggers_callbacks():
    """settings.reload() should invoke registered callbacks with new values."""
    config_file = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    config_file.write(json.dumps({
        "dispatcher_concurrency": 42,
        "global_rate_limit_per_sec": 777,
        "health_check_interval": 99,
    }))
    config_file.close()

    captured = {}

    def on_reload(s):
        captured["concurrency"] = s.DISPATCHER_CONCURRENCY
        captured["rate"] = s.GLOBAL_RATE_LIMIT_PER_SEC
        captured["interval"] = s.HEALTH_CHECK_INTERVAL

    with patch.dict(os.environ, {}, clear=False):
        with patch("webhook_courier.config._CONFIG_FILE", config_file.name):
            s = Settings()
            s.on_reload(on_reload)
            s.reload()

    assert captured["concurrency"] == 42
    assert captured["rate"] == 777
    assert captured["interval"] == 99

    os.unlink(config_file.name)
