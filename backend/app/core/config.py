"""Application configuration.

All configuration is read from environment variables (12-factor). Nothing secret has a
default value; secrets must be provided by the deployment (``.env``, Kubernetes Secret,
external secret operator, ...).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DBM_", env_file=None, extra="ignore")

    # --- runtime -----------------------------------------------------------------------
    env: Literal["development", "test", "production"] = "production"
    process_role: Literal["all", "api", "worker"] = "all"
    log_level: str = "INFO"
    log_json: bool = True
    app_name: str = "DB Manager"
    public_base_url: str = "http://localhost:8080"

    # --- application database -----------------------------------------------------------
    database_url: SecretStr = Field(
        default=SecretStr(""),
        description="postgresql+asyncpg://user:pass@host:5432/db",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 10
    run_migrations_on_start: bool = True

    # --- security -----------------------------------------------------------------------
    master_key: SecretStr = Field(default=SecretStr(""), description="base64 encoded 32-byte KEK")
    master_key_id: str = "k1"
    previous_master_keys: SecretStr = Field(
        default=SecretStr(""), description="comma separated id:base64key entries for decryption only"
    )
    session_ttl_minutes: int = 8 * 60
    session_idle_timeout_minutes: int = 30
    cookie_secure: bool = True
    cookie_domain: str | None = None
    cors_origins: CsvList = Field(default_factory=list)
    trusted_proxies: CsvList = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    login_rate_per_ip_per_minute: int = 20
    api_rate_per_user_per_minute: int = 600
    #: API tokens: required expiry, at most this many days
    api_token_max_days: int = Field(default=365, ge=1, le=3650)
    #: failed Bearer authentications per source IP and minute before 429
    api_token_auth_failures_per_minute: int = 30
    secret_file_dirs: CsvList = Field(
        default_factory=lambda: ["/run/secrets", "/var/run/secrets", "/etc/dbmanager/secrets"]
    )

    # --- bootstrap --------------------------------------------------------------------------
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: SecretStr = Field(default=SecretStr(""))
    bootstrap_admin_email: str = "admin@example.local"

    # --- demo (deprecated) -----------------------------------------------------------------
    # Demo data is generated on request under Platform -> Simulation; these are read only to
    # warn about leftover settings.
    demo_mode: bool = False

    # --- collectors -------------------------------------------------------------------------
    collector_concurrency: int = 16
    collector_batch_size: int = 32
    scheduler_tick_seconds: int = 15
    worker_poll_seconds: float = 2.0
    target_connect_timeout_seconds: float = 8.0
    target_statement_timeout_seconds: float = 30.0
    # legacy PostgreSQL metric tables (metric_samples / query_stat_snapshots) are pruned after
    # this many days; new telemetry lives in the time-series database
    metrics_retention_days: int = 35
    job_history_retention_days: int = 7
    circuit_failure_threshold: int = 3

    # --- time-series storage (monitoring telemetry) -----------------------------------------
    timeseries_enabled: bool = True
    #: victoriametrics (production) | memory (tests / development without the container)
    timeseries_backend: Literal["victoriametrics", "memory"] = "victoriametrics"
    timeseries_url: str = "http://timeseries:8428"
    timeseries_username: str = ""
    timeseries_password: SecretStr = Field(default=SecretStr(""))
    timeseries_timeout_seconds: float = 10.0
    #: retention the time-series container was started with (informational; VictoriaMetrics
    #: enforces its own -retentionPeriod). Format: 30d, 90d, 1y …
    timeseries_retention: str = "90d"
    metric_batch_size: int = 2000
    metric_flush_interval: float = 5.0
    #: samples kept in memory while the time-series database is unreachable (oldest dropped first)
    metric_buffer_max: int = 200_000
    metric_write_max_backoff_seconds: float = 60.0
    #: also write samples to the legacy PostgreSQL table during a staged migration (phase B-D)
    metrics_pg_dual_write: bool = False
    #: query telemetry cardinality guards
    query_stats_top_n: int = 50
    query_fingerprints_max_per_server: int = 2000

    # --- collection interval classes (seconds) ----------------------------------------------
    interval_fast_seconds: int = 30
    interval_normal_seconds: int = 60
    interval_slow_seconds: int = 300
    interval_very_slow_seconds: int = 3600
    #: spread scheduled collections over this share of their interval (0 disables jitter)
    collection_jitter_ratio: float = 0.2

    # --- workspace --------------------------------------------------------------------------
    workspace_max_rows: int = 5000
    workspace_default_rows: int = 500
    workspace_timeout_seconds: float = 30.0

    # --- product / about -----------------------------------------------------------------
    #: build metadata baked into images (Dockerfile ARG → ENV); empty in source checkouts
    build_version: str = ""
    git_commit: str = ""
    build_date: str = ""
    #: links shown in the UI (About page, Help menu); an empty documentation URL hides the entry
    documentation_url: str = ""
    github_url: str = "https://github.com/hbenabdalla/dbmanager"
    roadmap_url: str = ""
    #: license shown on the About page; empty = the project license (``app.__license__``)
    license: str = ""

    # --- alerting & notifications ------------------------------------------------------------
    alert_evaluation_seconds: int = 60
    #: webhook targets on private/loopback/link-local addresses are refused unless enabled
    webhook_allow_private: bool = False
    webhook_timeout_seconds: float = 10.0
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = Field(default=SecretStr(""))
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_timeout_seconds: float = 15.0

    # --- AI insights (architected, disabled) ---------------------------------------------
    ai_insights_enabled: bool = False

    @field_validator("cors_origins", "trusted_proxies", "secret_file_dirs", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    @property
    def is_dev(self) -> bool:
        return self.env in ("development", "test")

    @property
    def runs_api(self) -> bool:
        return self.process_role in ("all", "api")

    @property
    def runs_worker(self) -> bool:
        return self.process_role in ("all", "worker")


PLACEHOLDER_MARKER = "CHANGE_ME"
SETUP_HINT = (
    "Fill .env with generated secrets: on Windows run "
    "'powershell -ExecutionPolicy Bypass -File scripts\\init-env.ps1', elsewhere './scripts/init-env.sh'. "
    "If the database volume was created with the placeholder password, recreate it first with "
    "'docker compose down -v' (this deletes all DB Manager data)."
)


def placeholder_settings(s: Settings) -> list[str]:
    """Names of settings that still carry a CHANGE_ME value copied from .env.example."""
    checks = {
        "DBM_MASTER_KEY": s.master_key.get_secret_value(),
        "DBM_BOOTSTRAP_ADMIN_PASSWORD": s.bootstrap_admin_password.get_secret_value(),
        "POSTGRES_PASSWORD (in DBM_DATABASE_URL)": s.database_url.get_secret_value(),
    }
    return [name for name, value in checks.items() if PLACEHOLDER_MARKER in value]


@lru_cache
def get_settings() -> Settings:
    return Settings()
