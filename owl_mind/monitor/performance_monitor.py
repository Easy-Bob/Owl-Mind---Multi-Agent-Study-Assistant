"""Online monitoring, anomaly detection, and routing penalty feedback.

STUB. Implements ISSUE-001 FR2. Behaviour lands with the monitor issue;
see plan section 11.1 (highlight 7).

The point of this module is that monitoring feeds back into routing rather than
only into a dashboard: an agent whose success rate drops or whose latency rises
gets a ``monitor_penalty``, which lowers its routing_score, which makes it less
likely to be selected from the pool.

Prerequisite (plan 11.4): per-tool traces must actually reach the orchestrator
result. In the reference implementation they were assigned only on the
tool-loop exhaustion path, so every successful request reported no traces and
this module's tool metrics measured nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Anomaly:
    """One detected problem, with the suggestion that follows from it."""

    subject: str
    metric: str
    observed: float
    threshold: float
    suggestion: str


class PerformanceMonitor:
    """Samples agent and tool statistics, emits penalties and alerts."""

    async def collect(self) -> None:
        raise NotImplementedError(
            "PerformanceMonitor.collect is a scaffold stub (ISSUE-001). "
            "Implemented by the monitor issue."
        )

    def detect_anomalies(self) -> list[Anomaly]:
        raise NotImplementedError(
            "PerformanceMonitor.detect_anomalies is a scaffold stub (ISSUE-001)."
        )

    def summary(self) -> dict[str, Any]:
        raise NotImplementedError(
            "PerformanceMonitor.summary is a scaffold stub (ISSUE-001)."
        )
