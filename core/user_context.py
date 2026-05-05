"""
core/user_context.py

UserContext — the multi-tenant scoping layer.

This is the architecture answer to Alisson's bank example:
a bank customer and a bank associate query the same underlying
connector but get completely different views.

The connector logic is identical. What changes is:
  - scope: what device_ids the user is allowed to query
  - role: what the agent treats as actionable vs noise
  - thresholds: what severity levels apply to this user
  - display: what the connector is called for this user

Example:
    # Bank customer — sees only their own account
    ctx = UserContext(
        user_id="customer_123",
        role=Role.END_USER,
        scope=Scope.SINGLE,
        allowed_device_ids=["device_abc"],
        severity_floor=Severity.WARNING,
    )

    # Bank associate — sees all accounts they manage
    ctx = UserContext(
        user_id="associate_456",
        role=Role.OPERATOR,
        scope=Scope.FLEET,
        allowed_device_ids=["device_abc", "device_def", ...],
        severity_floor=Severity.INFO,
    )

The same connector.fetch() call returns different data because
the device_id scope is different. The same agent reasoning produces
different output because the severity thresholds are different.
A 2% packet loss on one device is noise for an operator watching
500 devices. It is a critical issue for the end user on that device.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from core.schema import Severity


class Role(str, Enum):
    """
    User role — determines what the agent treats as signal vs noise.

    END_USER:  Individual user. Single-device scope. High sensitivity —
               any degradation affects their work directly.
    OPERATOR:  IT staff or MSP. Multi-device scope. Threshold-based —
               only surfaces issues that affect enough devices to warrant
               intervention.
    ADMIN:     Full fleet access. All severities. Used for auditing and
               root cause analysis.
    """
    END_USER = "end_user"
    OPERATOR = "operator"
    ADMIN = "admin"


class Scope(str, Enum):
    """
    Query scope — determines what device_ids are visible to this user.

    SINGLE:     One device. Used for end users querying their own machine.
    TEAM:       A defined set of devices. Used for team leads or department heads.
    FLEET:      All devices the user has access to. Used for operators and admins.
    """
    SINGLE = "single"
    TEAM = "team"
    FLEET = "fleet"


@dataclass
class SeverityThreshold:
    """
    Role-specific severity thresholds.

    Defines what counts as actionable for a given role.
    An end user cares about WARNING on their single device.
    An operator only pages on CRITICAL affecting 10%+ of fleet.
    """
    minimum_severity: Severity = Severity.WARNING
    fleet_critical_pct: float = 0.0
    fleet_warning_pct: float = 0.0

    def is_actionable(self, severity: Severity, affected_pct: float = 100.0) -> bool:
        severity_order = [Severity.OK, Severity.INFO, Severity.WARNING, Severity.CRITICAL]
        severity_idx = severity_order.index(severity)
        minimum_idx = severity_order.index(self.minimum_severity)

        if severity_idx < minimum_idx:
            return False

        if self.fleet_critical_pct > 0 and severity == Severity.CRITICAL:
            return affected_pct >= self.fleet_critical_pct

        if self.fleet_warning_pct > 0 and severity == Severity.WARNING:
            return affected_pct >= self.fleet_warning_pct

        return True


# Default thresholds per role
ROLE_THRESHOLDS: dict[Role, SeverityThreshold] = {
    Role.END_USER: SeverityThreshold(
        minimum_severity=Severity.WARNING,
        fleet_critical_pct=0.0,
        fleet_warning_pct=0.0,
    ),
    Role.OPERATOR: SeverityThreshold(
        minimum_severity=Severity.WARNING,
        fleet_critical_pct=5.0,
        fleet_warning_pct=20.0,
    ),
    Role.ADMIN: SeverityThreshold(
        minimum_severity=Severity.INFO,
        fleet_critical_pct=0.0,
        fleet_warning_pct=0.0,
    ),
}


@dataclass
class UserContext:
    """
    Scoping context for a single user query.

    Passed into agent calls to scope the snapshot and calibrate
    what the agent treats as actionable. The connector itself
    does not change — only the scope and thresholds change.
    """
    user_id: str
    role: Role = Role.END_USER
    scope: Scope = Scope.SINGLE
    allowed_device_ids: list[str] = field(default_factory=list)
    organization_id: Optional[str] = None
    custom_thresholds: Optional[SeverityThreshold] = None

    @property
    def thresholds(self) -> SeverityThreshold:
        if self.custom_thresholds:
            return self.custom_thresholds
        return ROLE_THRESHOLDS[self.role]

    def can_access_device(self, device_id: str) -> bool:
        if self.scope == Scope.FLEET:
            return True
        if self.scope == Scope.SINGLE:
            return device_id in self.allowed_device_ids
        if self.scope == Scope.TEAM:
            return device_id in self.allowed_device_ids
        return False

    def filter_device_ids(self, device_ids: list[str]) -> list[str]:
        if self.scope == Scope.FLEET:
            return device_ids
        return [d for d in device_ids if self.can_access_device(d)]

    def is_actionable(self, severity: Severity, affected_pct: float = 100.0) -> bool:
        return self.thresholds.is_actionable(severity, affected_pct)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "role": self.role.value,
            "scope": self.scope.value,
            "organization_id": self.organization_id,
            "thresholds": {
                "minimum_severity": self.thresholds.minimum_severity.value,
                "fleet_critical_pct": self.thresholds.fleet_critical_pct,
                "fleet_warning_pct": self.thresholds.fleet_warning_pct,
            },
        }


def make_end_user_context(user_id: str, device_id: str) -> UserContext:
    """Convenience factory for a single end user."""
    return UserContext(
        user_id=user_id,
        role=Role.END_USER,
        scope=Scope.SINGLE,
        allowed_device_ids=[device_id],
    )


def make_operator_context(user_id: str, org_id: str) -> UserContext:
    """Convenience factory for an operator managing a full org fleet."""
    return UserContext(
        user_id=user_id,
        role=Role.OPERATOR,
        scope=Scope.FLEET,
        organization_id=org_id,
    )


def make_admin_context(user_id: str) -> UserContext:
    """Convenience factory for a full admin."""
    return UserContext(
        user_id=user_id,
        role=Role.ADMIN,
        scope=Scope.FLEET,
    )
