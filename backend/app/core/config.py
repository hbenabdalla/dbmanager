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
    app_name: str = "KBM DBManager"
    public_base_url: str = "http://localhost:8080"
    #: how often each process reports itself in platform_instances (stale after 3 intervals)
    instance_heartbeat_seconds: int = Field(default=15, ge=5, le=300)

    # --- application database -----------------------------------------------------------
    database_url: SecretStr = Field(
        default=SecretStr(""),
        description="postgresql+asyncpg://user:pass@host:5432/db",
    )
    #: TLS to the control-plane PostgreSQL: "" keeps what DBM_DATABASE_URL says (asyncpg's default
    #: is ``prefer``); require = encrypted, no certificate check; verify-ca = certificate signed by
    #: DBM_DATABASE_TLS_CA_FILE (or the system CAs); verify-full = also the host name
    database_tls_mode: Literal["", "disable", "prefer", "require", "verify-ca", "verify-full"] = ""
    database_tls_ca_file: str = ""
    database_pool_size: int = 10
    database_max_overflow: int = 10
    #: connections reserved for the rate limiter (``DBM_RATE_LIMIT_BACKEND=database``); a pool of
    #: its own, so requests holding the main pool can never starve the limiter
    rate_limit_pool_size: int = Field(default=5, ge=1)
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
    #: TOTP multi-factor authentication for local accounts: none | admins (any platform-wide
    #: administrative permission) | all. Users who must use MFA and have not enrolled can only
    #: reach the enrollment endpoints until they do.
    mfa_required_for: Literal["none", "admins", "all"] = "none"
    mfa_issuer: str = "KBM DBManager"
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # --- rate limiting (app.services.ratelimit) -------------------------------------------
    #: ``database`` = counters in PostgreSQL, shared by every backend replica; ``memory`` =
    #: per process (single-instance installations, tests)
    rate_limit_backend: Literal["database", "memory"] = "database"
    login_rate_per_ip_per_minute: int = Field(default=20, ge=0)
    #: every authenticated API request, per principal (a user session or one API token); 0 = off
    api_rate_per_user_per_minute: int = Field(default=600, ge=0)
    #: requests that query a managed database live (sessions, locks, objects, configuration …)
    api_rate_target_per_minute: int = Field(default=120, ge=0)
    #: expensive operations: SQL execution, plans, administration, exports, connection tests …
    api_rate_expensive_per_minute: int = Field(default=30, ge=0)
    #: API tokens: required expiry, at most this many days
    api_token_max_days: int = Field(default=365, ge=1, le=3650)
    #: failed Bearer authentications per source IP and minute before 429
    api_token_auth_failures_per_minute: int = Field(default=30, ge=0)
    # --- HashiCorp Vault (credential provider "vault", app.services.vault) --------------------
    #: e.g. https://vault.example.com:8200; empty = the vault provider is not available
    vault_addr: str = ""
    vault_namespace: str = ""
    #: token | approle | kubernetes
    vault_auth: Literal["token", "approle", "kubernetes"] = "token"
    vault_token: SecretStr = Field(default=SecretStr(""))
    vault_token_file: str = ""
    vault_role_id: str = ""
    vault_secret_id: SecretStr = Field(default=SecretStr(""))
    vault_secret_id_file: str = ""
    vault_kubernetes_role: str = ""
    vault_kubernetes_jwt_file: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    vault_auth_mount: str = ""  # default: approle / kubernetes
    #: KV version 2 mount used when a reference does not name one
    vault_kv_mount: str = "secret"
    vault_ca_file: str = ""
    vault_timeout_seconds: float = 10.0
    #: resolved secrets are kept in memory this long (0 = read Vault for every connection)
    vault_cache_seconds: int = Field(default=60, ge=0, le=3600)
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
    #: CA bundle (PEM) that signed the certificate of an ``https://`` DBM_TIMESERIES_URL
    timeseries_tls_ca_file: str = ""
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
    #: links shown in the UI (About page, Help menu). Documentation: empty = the published project
    #: documentation (``app.DOCUMENTATION_URL``), a URL = your own copy, ``off`` = hide the entry
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
    #: deprecated alias of smtp_security=starttls|none (kept for existing installations)
    smtp_starttls: bool = True
    #: starttls (submission, port 587) | tls (implicit TLS, SMTPS port 465) | none (plain, internal relays)
    #: empty = derived from smtp_starttls
    smtp_security: Literal["", "starttls", "tls", "none"] = ""
    #: CA bundle to verify the SMTP server certificate (internal CAs); empty = system trust store
    smtp_ca_file: str = ""
    smtp_timeout_seconds: float = 15.0
    #: largest report file e-mailed by a schedule (larger files fail the run with a clear reason)
    report_max_attachment_mb: float = 10.0
    #: directory with operator-supplied, licensed engine logos (<engine>.svg|png); empty = the
    #: original DBManager engine marks only (see docs/development/brand-assets.md)
    branding_dir: str = ""
    #: delivery attempts per notification (first try + retries with backoff), and the backoff steps
    notify_max_attempts: int = Field(default=5, ge=1, le=20)
    notify_retry_backoff_seconds: CsvList = Field(default_factory=lambda: ["60", "300", "900", "3600"])

    # --- backup & restore (app.backup) -------------------------------------------------------
    #: file-system storage targets (local disk, a mounted volume, an NFS mount) must live under one
    #: of these directories; DBManager never writes or deletes outside them
    backup_allowed_roots: CsvList = Field(default_factory=lambda: ["/var/lib/dbmanager/backups"])
    #: scratch space for building and verifying archives (default: the system temp directory)
    backup_work_dir: str = ""
    #: backup jobs running at the same time in one worker process
    backup_concurrency: int = Field(default=1, ge=1, le=16)
    #: a running backup job without a heartbeat for this long is marked FAILED (worker lost)
    backup_stale_minutes: int = Field(default=30, ge=5)
    #: decryption identity for age-recipient backups used by scheduled verification / restore:
    #: a file holding an AGE-SECRET-KEY-1… line (never stored in the database)
    backup_identity_file: str = ""
    #: external tool binaries (PATH lookup when empty)
    backup_pg_bin_dir: str = ""
    backup_mysql_bin_dir: str = ""
    #: an external tool run longer than this is cancelled
    backup_tool_timeout_seconds: int = Field(default=6 * 3600, ge=60)

    @field_validator(
        "cors_origins",
        "trusted_proxies",
        "secret_file_dirs",
        "notify_retry_backoff_seconds",
        "backup_allowed_roots",
        mode="before",
    )
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
    "'docker compose down -v' (this deletes all KBM DBManager data)."
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
