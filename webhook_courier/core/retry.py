import random
import math


def calculate_backoff(
    base_interval: float,
    attempt: int,
    jitter: str = "full",
    max_backoff: float = 3600.0,
    last_delay: float | None = None,
) -> float:
    """Calculate retry backoff delay with configurable jitter strategy.

    Strategies (per AWS Architecture Blog):
    - none: pure exponential, base * 2^attempt
    - full: random in [0, base * 2^attempt]
    - equal: half exponential + half random
    - decorrelated: random in [base, last_delay * 3]
    """
    exp_backoff = base_interval * (2 ** attempt)
    exp_backoff = min(exp_backoff, max_backoff)

    if jitter == "none":
        return exp_backoff
    elif jitter == "full":
        return random.uniform(0, exp_backoff)
    elif jitter == "equal":
        half = exp_backoff / 2
        return half + random.uniform(0, half)
    elif jitter == "decorrelated":
        prev = last_delay if last_delay is not None else base_interval
        return min(max_backoff, random.uniform(base_interval, prev * 3))
    else:
        return exp_backoff
