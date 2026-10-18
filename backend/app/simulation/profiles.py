"""Simulation profiles: how large and how deep a generated demo estate is.

Profiles are plain configuration objects. Add a profile by calling :func:`register_profile`
(for example from a plugin or a test); nothing else in the code base hard-codes their sizes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SimulationProfile:
    key: str
    label: str
    description: str
    #: servers per environment; the sum is the fleet size
    environments: dict[str, int]
    #: number of business applications the servers are spread over
    applications: int
    #: days of metric history generated per server
    history_days: int
    #: hours of hourly query-statistics snapshots generated per server
    query_history_hours: int
    #: metric resolution as (age limit in hours, step in minutes), youngest first
    resolution: tuple[tuple[float, int], ...]
    #: include cloud-managed services (RDS, Aurora, Azure SQL, Autonomous Database, Db2 on Cloud)
    include_cloud: bool = True
    #: collection interval given to the simulated servers
    collection_interval_s: int = 60
    engines: tuple[str, ...] = field(default=("postgresql", "oracle", "db2", "mssql", "mysql", "mariadb"))

    @property
    def servers(self) -> int:
        return sum(self.environments.values())

    def sample_times(self) -> int:
        """Number of metric timestamps generated per server (for estimates and tests)."""
        total, prev_h = 0, 0.0
        limit = self.history_days * 24
        for age_h, step_min in self.resolution:
            upper = min(age_h, limit)
            if upper > prev_h:
                total += int((upper - prev_h) * 60 / step_min)
            prev_h = upper
        return total

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "servers": self.servers,
            "environments": dict(self.environments),
            "applications": self.applications,
            "history_days": self.history_days,
            "query_history_hours": self.query_history_hours,
            "include_cloud": self.include_cloud,
            "engines": list(self.engines),
            "estimated_metric_rows": self.sample_times() * self.servers * 20,
        }


PROFILES: dict[str, SimulationProfile] = {}


def register_profile(profile: SimulationProfile) -> SimulationProfile:
    PROFILES[profile.key] = profile
    return profile


def get_profile(key: str) -> SimulationProfile:
    try:
        return PROFILES[key]
    except KeyError as exc:
        raise KeyError(f"unknown simulation profile {key!r}; available: {', '.join(PROFILES)}") from exc


register_profile(
    SimulationProfile(
        key="small",
        label="Small",
        description="5 servers across production and pre-production, one day of metrics. Quick to generate.",
        environments={"Production": 2, "UAT": 1, "Development": 1, "Test": 1},
        applications=5,
        history_days=1,
        query_history_hours=12,
        resolution=((6, 1), (24, 5)),
        include_cloud=False,
    )
)
register_profile(
    SimulationProfile(
        key="standard",
        label="Standard",
        description="18 servers in five environments with clusters, cloud services and seven days of history.",
        environments={"Production": 7, "DR": 3, "UAT": 3, "Development": 3, "Test": 2},
        applications=8,
        history_days=7,
        query_history_hours=24,
        resolution=((6, 1), (48, 5), (168, 15)),
    )
)
register_profile(
    SimulationProfile(
        key="large",
        label="Large",
        description="75 servers, every supported engine and cloud service, 30 days of history. Takes a few minutes.",
        environments={"Production": 28, "DR": 12, "UAT": 13, "Development": 14, "Test": 8},
        applications=16,
        history_days=30,
        query_history_hours=24,
        resolution=((2, 1), (24, 5), (168, 15), (720, 60)),
        collection_interval_s=120,
    )
)
