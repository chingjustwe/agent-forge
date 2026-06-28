import threading
from collections import defaultdict


class MetricsCollector:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple, int]] = defaultdict(lambda: defaultdict(int))
        self._histograms: dict[str, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))

    def increment_counter(self, name: str, tags: dict | None = None) -> None:
        key = tuple(sorted((tags or {}).items()))
        with self._lock:
            self._counters[name][key] += 1

    def counter(self, name: str, tags: dict | None = None) -> int:
        key = tuple(sorted((tags or {}).items()))
        with self._lock:
            return self._counters[name].get(key, 0)

    def observe_histogram(self, name: str, value: float, tags: dict | None = None) -> None:
        key = tuple(sorted((tags or {}).items()))
        with self._lock:
            vals = self._histograms[name][key]
            vals.append(value)
            if len(vals) > 10000:
                vals.pop(0)

    def histogram_snapshot(self, name: str, tags: dict | None = None) -> dict:
        key = tuple(sorted((tags or {}).items()))
        with self._lock:
            vals = sorted(self._histograms[name].get(key, []))
        if not vals:
            return {"count": 0, "p50": 0, "p95": 0, "p99": 0}
        n = len(vals)
        return {
            "count": n,
            "p50": vals[int(n * 0.5)],
            "p95": vals[int(n * 0.95)],
            "p99": vals[int(n * 0.99)],
        }

    def total_counter(self, name: str) -> int:
        with self._lock:
            return sum(self._counters[name].values())

    def all_counters(self) -> dict[str, dict[tuple, int]]:
        with self._lock:
            return {k: dict(v) for k, v in self._counters.items()}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._histograms.clear()


metrics = MetricsCollector()
