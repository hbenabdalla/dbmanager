"""Plan a realistic, deterministic enterprise database estate.

The planner only decides *what* exists — applications, sites, clusters, servers and the
operating condition of each server. The values the servers report are produced later by the
simulation adapter from each server's scenarios, so a server planned as *critical* reports the
metrics that make the health model rate it critical (and the rule engines explain why).

Same profile + same seed => the same plan (names, topology, conditions).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

from app.simulation.profiles import SimulationProfile

# name, business service, owning team, engine, cloud deployment (used when the profile includes cloud)
APPLICATIONS: list[tuple[str, str, str, str, str | None]] = [
    ("Order Management", "Commerce", "team-commerce", "postgresql", None),
    ("ERP Financials", "Finance", "team-erp", "oracle", None),
    ("Core Banking", "Banking", "team-core", "db2", None),
    ("CRM", "Sales", "team-crm", "mssql", None),
    ("Web Shop", "Commerce", "team-web", "mysql", "aws-rds"),
    ("Payments", "Commerce", "team-payments", "postgresql", "aws-aurora"),
    ("Customer Portal", "Digital", "team-digital", "mariadb", None),
    ("Data Warehouse", "Data", "team-data", "postgresql", None),
    ("HR Payroll", "People", "team-hr", "oracle", "oci-autonomous"),
    ("Treasury", "Finance", "team-treasury", "db2", "ibm-db2-cloud"),
    ("Supply Chain", "Operations", "team-scm", "mssql", None),
    ("Loyalty", "Marketing", "team-loyalty", "mysql", None),
    ("Claims", "Insurance", "team-claims", "oracle", None),
    ("Reporting Hub", "Data", "team-bi", "mssql", "azure-sql"),
    ("Identity Service", "Platform", "team-iam", "postgresql", "aws-rds"),
    ("Billing", "Finance", "team-billing", "postgresql", None),
    ("Logistics", "Operations", "team-logistics", "mariadb", None),
    ("Risk Engine", "Banking", "team-risk", "db2", None),
]

ENV_CODE = {"Production": "PROD", "DR": "DR", "UAT": "UAT", "Development": "DEV", "Test": "TEST"}
ENV_ORDER = ["Production", "DR", "UAT", "Development", "Test"]
ENGINE_PREFIX = {
    "postgresql": "PG",
    "oracle": "ORA",
    "db2": "DB2",
    "mssql": "MSSQL",
    "mysql": "MYSQL",
    "mariadb": "MARIADB",
}
CLOUD_PREFIX = {
    ("aws-rds", "postgresql"): "RDS-PG",
    ("aws-rds", "mysql"): "RDS-MYSQL",
    ("aws-aurora", "postgresql"): "AURORA-PG",
    ("azure-sql", "mssql"): "AZSQL",
    ("oci-autonomous", "oracle"): "ADB",
    ("ibm-db2-cloud", "db2"): "DB2C",
}
CLOUD_PROVIDER = {
    "aws-rds": "aws",
    "aws-aurora": "aws",
    "azure-sql": "azure",
    "oci-autonomous": "oci",
    "ibm-db2-cloud": "ibm",
}
# provider -> (primary site, DR site): (name, provider, region)
SITES: dict[str, tuple[tuple[str, str, str], tuple[str, str, str]]] = {
    "on-prem": (("DC-East", "on-prem", "east"), ("DC-West", "on-prem", "west")),
    "aws": (("AWS eu-central-1", "aws", "eu-central-1"), ("AWS eu-west-1", "aws", "eu-west-1")),
    "azure": (("Azure West Europe", "azure", "westeurope"), ("Azure North Europe", "azure", "northeurope")),
    "oci": (("OCI Frankfurt", "oci", "eu-frankfurt-1"), ("OCI Amsterdam", "oci", "eu-amsterdam-1")),
    "ibm": (("IBM Cloud Frankfurt", "ibm", "eu-de"), ("IBM Cloud London", "ibm", "eu-gb")),
}
PORTS = {"postgresql": 5432, "oracle": 1521, "db2": 50000, "mssql": 1433, "mysql": 3306, "mariadb": 3306}
CLUSTER_KIND = {
    "postgresql": "primary-replica",
    "oracle": "rac",
    "db2": "hadr",
    "mssql": "availability-group",
    "mysql": "replication",
    "mariadb": "replication",
}
SIZE_GB = {
    "postgresql": (300, 900),
    "oracle": (1500, 3500),
    "db2": (1500, 3800),
    "mssql": (300, 900),
    "mysql": (80, 300),
    "mariadb": (60, 200),
}

# Operating conditions -> scenario sets. Each produces the evidence the health model and the
# rule engines need, so the condition is explained by the data rather than assigned.
CRITICAL = [
    (["connection_saturation"], "Connection pool exhausted at peak load"),
    (["cpu_saturation", "slow_queries"], "CPU saturated by expensive statements"),
    (["storage_full", "temp_spill"], "Data volume almost full"),
]
WARNING = [
    (["high_cpu", "slow_queries"], "High CPU and slow reporting queries"),
    (["blocking", "deadlocks"], "Lock contention and deadlocks"),
    (["storage_pressure"], "Storage above 90%"),
    (["cache_pressure", "high_cpu"], "Buffer cache too small for the working set"),
]


@dataclass
class ServerPlan:
    name: str
    engine: str
    environment: str
    application: str
    business_service: str
    owner: str
    site: str
    deployment: str
    cloud_provider: str | None
    port: int
    cluster: str | None = None
    cluster_role: str | None = None
    condition: str = "healthy"  # healthy | warning | critical | unavailable
    story: str = "Operating normally"
    scenarios: list[str] = field(default_factory=list)
    scale: float = 1.0
    size_gb: float = 100.0
    tls_mode: str = "verify-full"
    licensed_features: list[str] = field(default_factory=list)

    @property
    def role(self) -> str:
        return "replica" if self.cluster_role in ("replica", "standby") else "primary"

    @property
    def host(self) -> str:
        # .invalid is reserved (RFC 2606): the name can never resolve to a real system.
        return f"{self.name.lower()}.sim.invalid"


@dataclass
class FleetPlan:
    profile: str
    seed: int
    applications: list[tuple[str, str, str]]
    sites: list[tuple[str, str, str]]
    clusters: list[tuple[str, str]]
    servers: list[ServerPlan]

    def summary(self) -> dict[str, Any]:
        by: dict[str, dict[str, int]] = {"environment": {}, "engine": {}, "condition": {}}
        for s in self.servers:
            for k, v in (("environment", s.environment), ("engine", s.engine), ("condition", s.condition)):
                by[k][v] = by[k].get(v, 0) + 1
        return {"servers": len(self.servers), **by}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def plan_fleet(profile: SimulationProfile, seed: int) -> FleetPlan:
    rng = random.Random(f"{profile.key}:{seed}")  # noqa: S311 - reproducible demo data, not security
    apps = [a for a in APPLICATIONS if a[3] in profile.engines][: profile.applications]
    if not apps:
        raise ValueError("the profile selects no applications")

    def deployment_for(app: tuple[str, str, str, str, str | None]) -> str:
        return app[4] if (profile.include_cloud and app[4]) else "self-managed"

    counters: dict[tuple[str, str], int] = {}
    servers: list[ServerPlan] = []
    sites: dict[str, tuple[str, str, str]] = {}
    clusters: dict[str, str] = {}

    def add(app: tuple[str, str, str, str, str | None], env: str, cluster_role: str | None = None) -> ServerPlan:
        name_, service, owner, engine, _ = app
        deployment = deployment_for(app)
        provider = CLOUD_PROVIDER.get(deployment)
        prefix = CLOUD_PREFIX.get((deployment, engine), ENGINE_PREFIX[engine]) if provider else ENGINE_PREFIX[engine]
        key = (prefix, env)
        counters[key] = counters.get(key, 0) + 1
        site_pair = SITES[provider or "on-prem"]
        site = site_pair[1] if env in ("DR", "Development", "Test") else site_pair[0]
        sites[site[0]] = site
        lo, hi = SIZE_GB[engine]
        plan = ServerPlan(
            name=f"{prefix}-{ENV_CODE[env]}-{counters[key]:02d}",
            engine=engine,
            environment=env,
            application=name_,
            business_service=service,
            owner=owner,
            site=site[0],
            deployment=deployment,
            cloud_provider=provider,
            port=PORTS[engine],
            cluster_role=cluster_role,
            size_gb=round(rng.uniform(lo, hi) * (1 if env in ("Production", "DR") else rng.uniform(0.08, 0.35)), 1),
        )
        plan.scale = round(
            {
                "Production": rng.uniform(1.0, 1.45)
                if cluster_role in (None, "primary", "node")
                else rng.uniform(0.6, 0.9),
                "DR": rng.uniform(0.3, 0.45),
                "UAT": rng.uniform(0.4, 0.6),
                "Development": rng.uniform(0.15, 0.3),
                "Test": rng.uniform(0.2, 0.35),
            }[env],
            2,
        )
        if engine == "oracle" and env == "Production" and deployment == "self-managed":
            plan.licensed_features = ["diagnostics_pack", "tuning_pack"]
        servers.append(plan)
        return plan

    def cluster_for(app: tuple[str, str, str, str, str | None]) -> str:
        cname = f"{_slug(app[0])}-{'ha' if app[3] != 'oracle' else 'rac'}"
        clusters[cname] = CLUSTER_KIND[app[3]]
        return cname

    # --- production: primaries first, then HA nodes / replicas of the leading applications
    n_prod = profile.environments.get("Production", 0)
    # small estates: one server per application; larger ones keep ~30% for HA nodes / replicas
    n_primaries = min(len(apps), n_prod if n_prod <= 3 else max(1, round(n_prod * 0.7)))
    prod_primary: dict[str, ServerPlan] = {}
    for app in apps[:n_primaries]:
        prod_primary[app[0]] = add(app, "Production")
    for i in range(n_prod - n_primaries):
        app = apps[i % n_primaries]
        primary = prod_primary[app[0]]
        cname = primary.cluster or cluster_for(app)
        if primary.cluster is None:
            primary.cluster = cname
            primary.cluster_role = "node" if app[3] == "oracle" else "primary"
        add(app, "Production", "node" if app[3] == "oracle" else "replica").cluster = cname

    # --- DR: standbys of production systems, in priority order
    prod_apps = [a for a in apps if a[0] in prod_primary]
    for i in range(profile.environments.get("DR", 0)):
        app = prod_apps[i % len(prod_apps)] if prod_apps else apps[i % len(apps)]
        primary = prod_primary.get(app[0])
        standby = add(app, "DR", "standby")
        if primary is not None:
            cname = primary.cluster or cluster_for(app)
            if primary.cluster is None:
                primary.cluster = cname
                primary.cluster_role = "node" if app[3] == "oracle" else "primary"
            standby.cluster = cname

    # --- pre-production: rotate over all applications, continuing after the production ones
    pointer = n_primaries
    for env in ("UAT", "Development", "Test"):
        for _ in range(profile.environments.get(env, 0)):
            add(apps[pointer % len(apps)], env)
            pointer += 1

    _assign_conditions(servers, rng)
    _assign_security(servers, rng)
    for s in servers:
        if s.cluster_role in ("primary", "node") and any(
            o.cluster == s.cluster and o.cluster_role in ("replica", "standby") for o in servers
        ):
            s.scenarios.append("has_replicas")

    used_sites = [sites[k] for k in sites]
    used_apps = sorted({(s.application, s.business_service, s.owner) for s in servers})
    return FleetPlan(
        profile=profile.key,
        seed=seed,
        applications=used_apps,
        sites=used_sites,
        clusters=sorted(clusters.items()),
        servers=servers,
    )


def _assign_conditions(servers: list[ServerPlan], rng: random.Random) -> None:
    n = len(servers)
    n_down = max(1, round(n * 0.06))
    n_crit = max(1, round(n * 0.12))
    n_warn = max(1, round(n * 0.22))
    free = list(servers)

    def take(pool: list[ServerPlan], k: int) -> list[ServerPlan]:
        pool = [s for s in pool if s in free]
        rng.shuffle(pool)
        out = pool[:k]
        for s in out:
            free.remove(s)
        return out

    nonprod = [s for s in servers if s.environment in ("Test", "Development", "UAT")]
    for s in take(nonprod or servers, n_down):
        s.condition, s.story, s.scenarios = "unavailable", "Host unreachable (simulated outage)", ["down"]

    prod_primary = [s for s in servers if s.environment == "Production" and s.cluster_role in (None, "primary", "node")]
    crit = take(prod_primary, n_crit)
    crit += take([s for s in servers if s.environment == "Production"], n_crit - len(crit))
    crit += take(free, n_crit - len(crit))
    offset = rng.randrange(len(CRITICAL))
    for i, s in enumerate(crit):
        scen, story = CRITICAL[(offset + i) % len(CRITICAL)]
        s.condition, s.story, s.scenarios = "critical", story, list(scen)
        if "connection_saturation" in scen:
            s.scale = max(s.scale, 1.6)
        if "storage_full" in scen and s.engine in ("oracle", "db2", "mssql"):
            s.scenarios.append("tablespace_full")

    standbys = [s for s in free if s.cluster_role == "standby"]
    warn = take(standbys, min(len(standbys), max(1, n_warn // 3)))
    for s in warn:
        s.condition, s.story, s.scenarios = "warning", "Standby is falling behind the primary", ["replica_lag"]
    others = take(free, n_warn - len(warn))
    offset = rng.randrange(len(WARNING))
    for i, s in enumerate(others):
        scen, story = WARNING[(offset + i) % len(WARNING)]
        s.condition, s.story, s.scenarios = "warning", story, list(scen)
        if "storage_pressure" in scen and s.engine in ("oracle", "db2", "mssql"):
            s.scenarios.append("tablespace_full")

    # A healthy production primary whose hottest statement has started to regress: health is
    # still green but the query-performance pages and the advisor show the trend.
    healthy_prod = [s for s in free if s.environment == "Production" and s.cluster_role in (None, "primary", "node")]
    for s in take(healthy_prod, 1):
        s.scenarios.append("regressing_query")
        s.story = "Healthy, but the most frequent statement is getting slower"
    for s in free:
        if s.environment in ("UAT", "Development", "Test") and rng.random() < 0.3:
            s.scenarios.append("temp_spill")


def _assign_security(servers: list[ServerPlan], rng: random.Random) -> None:
    for s in servers:
        if s.environment in ("Production", "DR"):
            s.tls_mode = "verify-full"
            if rng.random() < 0.75:
                s.scenarios.append("hardened")
            elif rng.random() < 0.5:
                s.scenarios.append("weak_security")
        elif s.environment == "UAT":
            s.tls_mode = "require"
            if rng.random() < 0.3:
                s.scenarios.append("weak_security")
        else:
            s.tls_mode = rng.choice(["prefer", "disable", "require"])
            if rng.random() < 0.5:
                s.scenarios.append("weak_security")
    # end-of-support versions: mostly forgotten non-production systems
    candidates = [
        s for s in servers if s.environment in ("Development", "Test", "UAT") and s.condition != "unavailable"
    ]
    rng.shuffle(candidates)
    for s in candidates[: max(1, len(servers) // 12)]:
        s.scenarios.append("outdated_version")


def demo_profile(plan: ServerPlan, seed: int, regression_since: str) -> dict[str, Any]:
    """The per-server parameters stored in ``servers.options['demo_profile']``."""
    p: dict[str, Any] = {
        "engine": plan.engine,
        "seed": f"{seed}:{plan.name}",
        "scale": plan.scale,
        "scenarios": sorted(set(plan.scenarios)),
        "role": plan.role,
        "size_gb": plan.size_gb,
    }
    if "regressing_query" in plan.scenarios:
        p["regression_since"] = regression_since
    return p
