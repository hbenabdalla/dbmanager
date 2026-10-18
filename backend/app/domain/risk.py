from __future__ import annotations

from enum import StrEnum


class RiskClass(StrEnum):
    READ = "READ"
    LOW_RISK_CHANGE = "LOW_RISK_CHANGE"
    PRIVILEGED_CHANGE = "PRIVILEGED_CHANGE"
    DESTRUCTIVE = "DESTRUCTIVE"


RISK_ORDER = {
    RiskClass.READ: 0,
    RiskClass.LOW_RISK_CHANGE: 1,
    RiskClass.PRIVILEGED_CHANGE: 2,
    RiskClass.DESTRUCTIVE: 3,
}


def max_risk(*risks: RiskClass) -> RiskClass:
    return max(risks, key=lambda r: RISK_ORDER[r])
