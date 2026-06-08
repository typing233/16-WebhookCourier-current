import threading
import time
from collections import defaultdict


class MetricsCollector:
    """Lightweight Prometheus-compatible metrics collector.

    Supports counters, gauges, and histograms without external dependencies.
    Exposes metrics in Prometheus text exposition format.
    """

    HISTOGRAM_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf"))

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._gauges: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, dict] = {}

    def counter_inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None):
        label_key = self._label_key(labels)
        with self._lock:
            self._counters[name][label_key] += value

    def gauge_set(self, name: str, value: float, increment: bool = False):
        with self._lock:
            if increment:
                self._gauges[name] += value
            else:
                self._gauges[name] = value

    def histogram_observe(self, name: str, value: float, labels: dict[str, str] | None = None):
        label_key = self._label_key(labels)
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = {}
            if label_key not in self._histograms[name]:
                self._histograms[name][label_key] = {
                    "buckets": {b: 0 for b in self.HISTOGRAM_BUCKETS},
                    "sum": 0.0,
                    "count": 0,
                }
            h = self._histograms[name][label_key]
            h["sum"] += value
            h["count"] += 1
            for bucket in self.HISTOGRAM_BUCKETS:
                if value <= bucket:
                    h["buckets"][bucket] += 1

    def export_prometheus(self) -> str:
        lines = []
        with self._lock:
            for name, label_values in self._counters.items():
                full_name = f"webhook_courier_{name}"
                lines.append(f"# TYPE {full_name} counter")
                for label_key, value in label_values.items():
                    label_str = f"{{{label_key}}}" if label_key else ""
                    lines.append(f"{full_name}{label_str} {value}")

            for name, value in self._gauges.items():
                full_name = f"webhook_courier_{name}"
                lines.append(f"# TYPE {full_name} gauge")
                lines.append(f"{full_name} {value}")

            for name, label_values in self._histograms.items():
                full_name = f"webhook_courier_{name}"
                lines.append(f"# TYPE {full_name} histogram")
                for label_key, h in label_values.items():
                    label_prefix = f"{label_key}," if label_key else ""
                    cumulative = 0
                    for bucket, count in sorted(h["buckets"].items()):
                        cumulative += count
                        le = "+Inf" if bucket == float("inf") else str(bucket)
                        lines.append(f'{full_name}_bucket{{{label_prefix}le="{le}"}} {cumulative}')
                    lines.append(f"{full_name}_sum{{{label_key}}} {h['sum']}" if label_key else f"{full_name}_sum {h['sum']}")
                    lines.append(f"{full_name}_count{{{label_key}}} {h['count']}" if label_key else f"{full_name}_count {h['count']}")

        return "\n".join(lines) + "\n"

    def _label_key(self, labels: dict[str, str] | None) -> str:
        if not labels:
            return ""
        return ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))


metrics = MetricsCollector()
