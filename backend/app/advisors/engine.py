"""Deterministic, explainable rule engine for performance and security advice.

A rule inspects an :class:`EvidenceContext` (collected metrics, configuration, query
statistics, security evidence) and returns zero or more :class:`RuleFinding` objects.
Each finding carries the measured evidence that triggered it. Rules never change
anything on the target database.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

Severity = Literal["critical", "high", "medium", "low", "info"]
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class MetricWindow:
    avg: float
    min: float
    max: float
    last: float
    count: int
    p95: float | None = None


@dataclass
class EvidenceContext:
    server_id: str
    server_name: str
    engine: str
    environment_tier: str | None
    tls_mode: str
    is_demo: bool
    window_hours: float
    metrics: dict[str, MetricWindow] = field(default_factory=dict)
    latest: dict[str, float] = field(default_factory=dict)
    query_stats: list[dict[str, Any]] = field(default_factory=list)
    # results of sustained-condition checks on the time-series history, keyed by metric
    # (see app.services.advisor.SUSTAINED_CHECKS)
    sustained: dict[str, dict[str, Any]] = field(default_factory=dict)
    tuning: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    monitoring_username: str | None = None

    def m(self, name: str) -> MetricWindow | None:
        return self.metrics.get(name)


@dataclass
class RuleFinding:
    title: str
    finding: str
    evidence: dict[str, Any]
    recommendation: str
    risk: str
    confidence: float
    expected_impact: str = ""
    guidance: str | None = None
    affected_object: str | None = None
    database: str | None = None
    severity: Severity | None = None  # overrides the rule default
    remediation: str | None = None


@dataclass
class Rule:
    rule_id: str
    engines: tuple[str, ...]  # ("*",) = all engines
    kind: Literal["performance", "security"]
    category: str
    severity: Severity
    title: str
    description: str
    evaluate: Callable[[EvidenceContext], list[RuleFinding]]

    def applies_to(self, engine: str) -> bool:
        return "*" in self.engines or engine in self.engines


RULES: list[Rule] = []


def rule(
    rule_id: str,
    *,
    engines: tuple[str, ...],
    kind: Literal["performance", "security"],
    category: str,
    severity: Severity,
    title: str,
    description: str = "",
) -> Callable[[Callable[[EvidenceContext], list[RuleFinding]]], Callable[[EvidenceContext], list[RuleFinding]]]:
    def deco(fn: Callable[[EvidenceContext], list[RuleFinding]]) -> Callable[[EvidenceContext], list[RuleFinding]]:
        RULES.append(
            Rule(rule_id, engines, kind, category, severity, title, description or (fn.__doc__ or "").strip(), fn)
        )
        return fn

    return deco


@dataclass
class EvaluatedFinding:
    rule: Rule
    result: RuleFinding

    @property
    def severity(self) -> Severity:
        return self.result.severity or self.rule.severity

    def fingerprint(self, server_id: str) -> str:
        raw = f"{self.rule.rule_id}|{server_id}|{self.result.database or ''}|{self.result.affected_object or ''}"
        return hashlib.sha256(raw.encode()).hexdigest()[:40]


def evaluate(ctx: EvidenceContext, kind: Literal["performance", "security"]) -> list[EvaluatedFinding]:
    # Import rule modules so their decorators register.
    from app.advisors import performance, security  # noqa: F401

    out: list[EvaluatedFinding] = []
    for r in RULES:
        if r.kind != kind or not r.applies_to(ctx.engine):
            continue
        try:
            for f in r.evaluate(ctx) or []:
                out.append(EvaluatedFinding(r, f))
        except Exception:  # noqa: BLE001 - one broken rule must not stop the others
            log.exception("rule failed", extra={"rule_id": r.rule_id, "server": ctx.server_name})
    out.sort(key=lambda e: SEVERITY_ORDER[e.severity])
    return out


def catalog() -> list[dict[str, Any]]:
    from app.advisors import performance, security  # noqa: F401

    return [
        {
            "rule_id": r.rule_id,
            "engines": list(r.engines),
            "kind": r.kind,
            "category": r.category,
            "severity": r.severity,
            "title": r.title,
            "description": r.description,
        }
        for r in sorted(RULES, key=lambda r: r.rule_id)
    ]


def fmt_bytes(n: float | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} PiB"
