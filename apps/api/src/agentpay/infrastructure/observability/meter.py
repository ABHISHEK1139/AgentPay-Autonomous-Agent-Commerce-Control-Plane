"""Zero-dependency Prometheus-compatible metrics infrastructure (§47)."""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any


class Counter:
    """Monotonically increasing cumulative metric counter."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._lock = threading.Lock()
        # tuple of sorted (key, val) -> count
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def increment(self, amount: float = 1.0, labels: dict[str, str] | None = None) -> None:
        """Increments the counter by amount."""
        if amount < 0:
            raise ValueError("Counter cannot be decremented")
        label_key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._values[label_key] += amount

    def get_value(self, labels: dict[str, str] | None = None) -> float:
        """Gets current counter value for given labels."""
        label_key = tuple(sorted((labels or {}).items()))
        with self._lock:
            return self._values.get(label_key, 0.0)

    def collect(self) -> list[dict[str, Any]]:
        """Collects all label sets and values."""
        with self._lock:
            return [
                {"name": self.name, "labels": dict(labels), "value": val}
                for labels, val in self._values.items()
            ]


class Histogram:
    """Tracks the statistical distribution of numeric values (e.g. durations)."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(
        self, name: str, description: str = "", buckets: tuple[float, ...] = DEFAULT_BUCKETS
    ) -> None:
        self.name = name
        self.description = description
        self.buckets = sorted(buckets)
        self._lock = threading.Lock()
        # label_key -> list of values
        self._records: dict[tuple[tuple[str, str], ...], list[float]] = defaultdict(list)

    def record(self, value: float, labels: dict[str, str] | None = None) -> None:
        """Records an observed numeric value."""
        label_key = tuple(sorted((labels or {}).items()))
        with self._lock:
            self._records[label_key].append(value)

    def collect(self) -> list[dict[str, Any]]:
        """Collects histogram statistics."""
        with self._lock:
            results = []
            for labels, vals in self._records.items():
                count = len(vals)
                total = sum(vals)
                bucket_counts = {b: sum(1 for v in vals if v <= b) for b in self.buckets}
                results.append(
                    {
                        "name": self.name,
                        "labels": dict(labels),
                        "count": count,
                        "sum": total,
                        "avg": total / count if count else 0.0,
                        "buckets": bucket_counts,
                    }
                )
            return results


class Meter:
    """Metrics registry supporting Counter, Histogram, and Prometheus exposition (§47)."""

    def __init__(self, name: str = "agentpay") -> None:
        self.name = name
        self._lock = threading.Lock()
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}

    def create_counter(self, name: str, description: str = "") -> Counter:
        """Creates or returns an existing counter."""
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, description)
            return self._counters[name]

    def create_histogram(
        self,
        name: str,
        description: str = "",
        buckets: tuple[float, ...] = Histogram.DEFAULT_BUCKETS,
    ) -> Histogram:
        """Creates or returns an existing histogram."""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name, description, buckets)
            return self._histograms[name]

    def collect(self) -> dict[str, Any]:
        """Collects all metric snapshots into a dictionary."""
        with self._lock:
            counters = [c.collect() for c in self._counters.values()]
            histograms = [h.collect() for h in self._histograms.values()]
            return {"counters": counters, "histograms": histograms}

    def to_prometheus(self) -> str:
        """Formats all registered metrics into standard Prometheus exposition format."""
        lines: list[str] = []
        with self._lock:
            for counter in self._counters.values():
                if counter.description:
                    lines.append(f"# HELP {counter.name} {counter.description}")
                lines.append(f"# TYPE {counter.name} counter")
                for entry in counter.collect():
                    labels_str = ",".join(f'{k}="{v}"' for k, v in entry["labels"].items())
                    label_suffix = f"{{{labels_str}}}" if labels_str else ""
                    lines.append(f"{counter.name}{label_suffix} {entry['value']}")

            for hist in self._histograms.values():
                if hist.description:
                    lines.append(f"# HELP {hist.name} {hist.description}")
                lines.append(f"# TYPE {hist.name} histogram")
                for entry in hist.collect():
                    base_labels = entry["labels"]
                    for b_le, b_cnt in entry["buckets"].items():
                        b_lbls = dict(base_labels)
                        b_lbls["le"] = str(b_le)
                        b_str = ",".join(f'{k}="{v}"' for k, v in sorted(b_lbls.items()))
                        lines.append(f"{hist.name}_bucket{{{b_str}}} {b_cnt}")
                    b_inf = dict(base_labels)
                    b_inf["le"] = "+Inf"
                    inf_str = ",".join(f'{k}="{v}"' for k, v in sorted(b_inf.items()))
                    lines.append(f"{hist.name}_bucket{{{inf_str}}} {entry['count']}")
                    base_str = ",".join(f'{k}="{v}"' for k, v in sorted(base_labels.items()))
                    base_suffix = f"{{{base_str}}}" if base_str else ""
                    lines.append(f"{hist.name}_sum{base_suffix} {entry['sum']}")
                    lines.append(f"{hist.name}_count{base_suffix} {entry['count']}")

        return "\n".join(lines) + "\n"

    def clear(self) -> None:
        """Clears all registered metrics."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()
