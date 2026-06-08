from webhook_courier.core.retry import calculate_backoff


def test_no_jitter():
    delay = calculate_backoff(2.0, 0, jitter="none")
    assert delay == 2.0
    delay = calculate_backoff(2.0, 3, jitter="none")
    assert delay == 16.0


def test_full_jitter():
    delays = [calculate_backoff(2.0, 2, jitter="full") for _ in range(100)]
    assert all(0 <= d <= 8.0 for d in delays)
    assert min(delays) < 4.0  # randomness present


def test_equal_jitter():
    delays = [calculate_backoff(2.0, 2, jitter="equal") for _ in range(100)]
    assert all(4.0 <= d <= 8.0 for d in delays)


def test_decorrelated_jitter():
    delays = [calculate_backoff(2.0, 2, jitter="decorrelated", last_delay=4.0) for _ in range(100)]
    assert all(2.0 <= d <= 12.0 for d in delays)


def test_max_backoff_cap():
    delay = calculate_backoff(2.0, 20, jitter="none", max_backoff=100.0)
    assert delay == 100.0


def test_max_backoff_with_jitter():
    delay = calculate_backoff(2.0, 20, jitter="full", max_backoff=100.0)
    assert delay <= 100.0
