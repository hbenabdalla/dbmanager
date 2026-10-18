from __future__ import annotations

import ipaddress
import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORM

TlsMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]
ManagementMode = Literal["monitoring", "administration"]
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9_]([A-Za-z0-9_\-]{0,61}[A-Za-z0-9_])?\.)*[A-Za-z0-9_]([A-Za-z0-9_\-]{0,61}[A-Za-z0-9_])?$"
)


def check_options(v: dict[str, Any]) -> dict[str, Any]:
    """Engine connection options are small scalar settings (never credentials)."""
    if len(v) > 20:
        raise ValueError("too many connection options")
    for k, val in v.items():
        if not re.match(r"^[a-z][a-z0-9_]{0,40}$", k):
            raise ValueError(f"invalid option name {k!r}")
        if "password" in k or "secret" in k:
            raise ValueError("credentials are not connection options; use the credential fields")
        if val is not None and not isinstance(val, (str, int, float, bool)):
            raise ValueError(f"option {k!r} must be a scalar")
        if isinstance(val, str) and len(val) > 2000:
            raise ValueError(f"option {k!r} is too long")
    return v


class EnvironmentOut(ORM):
    id: uuid.UUID
    name: str
    tier: str
    description: str | None
    sort_order: int
    data_source: str = "real"  # real | simulated | system


class EnvironmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    tier: Literal["production", "dr", "uat", "development", "test"]
    description: str | None = None
    sort_order: int = 100


class SiteOut(ORM):
    id: uuid.UUID
    name: str
    region: str | None
    provider: str | None
    data_source: str = "real"


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    region: str | None = None
    provider: str | None = None


class ClusterOut(ORM):
    id: uuid.UUID
    name: str
    kind: str
    description: str | None
    data_source: str = "real"


class ClusterIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = "primary-replica"
    description: str | None = None


class ApplicationOut(ORM):
    id: uuid.UUID
    name: str
    business_service: str | None
    owner: str | None
    data_source: str = "real"


class ApplicationIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    business_service: str | None = None
    owner: str | None = None


class Tag(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-/]+$")
    value: str = Field("", max_length=256)


class CredentialIn(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str | None = Field(None, max_length=4096, description="write-only")
    provider: Literal["local", "env", "file"] = "local"
    secret_ref: str | None = Field(None, max_length=512)


class ServerBase(BaseModel):
    description: str | None = Field(None, max_length=2000)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    default_database: str | None = Field(None, max_length=128)
    deployment: str = "self-managed"
    cloud_provider: str | None = None
    cloud_resource_id: str | None = Field(None, max_length=512)
    environment_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    cluster_id: uuid.UUID | None = None
    cluster_role: str | None = None
    application_id: uuid.UUID | None = None
    owner: str | None = Field(None, max_length=128)
    business_service: str | None = Field(None, max_length=128)
    tls_mode: TlsMode = "verify-full"
    tls_ca_pem: str | None = Field(None, max_length=20000)
    monitoring_enabled: bool = True
    collection_interval_s: int = Field(60, ge=15, le=3600)
    licensed_features: list[str] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    #: monitoring = DB Manager never offers changes for this server, whatever the credentials allow
    management_mode: ManagementMode = "monitoring"
    #: engine-specific connection settings (Oracle service name/SID, SQL Server encryption, Db2 security…)
    connection_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("connection_options")
    @classmethod
    def _opts(cls, v: dict[str, Any]) -> dict[str, Any]:
        return check_options(v)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        try:
            ipaddress.ip_address(v)
            return v
        except ValueError:
            pass
        if not _HOST_RE.match(v):
            raise ValueError("invalid hostname")
        return v

    @field_validator("licensed_features")
    @classmethod
    def _lic(cls, v: list[str]) -> list[str]:
        allowed = {"diagnostics_pack", "tuning_pack", "query_store", "db2_connect", "advanced_security"}
        bad = [x for x in v if x not in allowed]
        if bad:
            raise ValueError(f"unknown licensed features: {bad}")
        return v


class ServerCreate(ServerBase):
    name: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9._\-]+$")
    engine: str
    monitoring_credential: CredentialIn | None = None
    admin_credential: CredentialIn | None = None


class ServerUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=128, pattern=r"^[A-Za-z0-9._\-]+$")
    description: str | None = None
    host: str | None = None
    port: int | None = Field(None, ge=1, le=65535)
    default_database: str | None = None
    deployment: str | None = None
    cloud_provider: str | None = None
    environment_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    cluster_id: uuid.UUID | None = None
    cluster_role: str | None = None
    application_id: uuid.UUID | None = None
    owner: str | None = None
    business_service: str | None = None
    tls_mode: TlsMode | None = None
    tls_ca_pem: str | None = None
    monitoring_enabled: bool | None = None
    collection_interval_s: int | None = Field(None, ge=15, le=3600)
    licensed_features: list[str] | None = None
    tags: list[Tag] | None = None
    management_mode: ManagementMode | None = None
    connection_options: dict[str, Any] | None = None

    @field_validator("connection_options")
    @classmethod
    def _opts(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return check_options(v) if v is not None else v


class TestConnectionRequest(BaseModel):
    engine: str
    host: str
    port: int = Field(ge=1, le=65535)
    default_database: str | None = None
    tls_mode: TlsMode = "verify-full"
    tls_ca_pem: str | None = None
    username: str
    password: str | None = None
    connection_options: dict[str, Any] = Field(default_factory=dict)
    licensed_features: list[str] = Field(default_factory=list)

    @field_validator("connection_options")
    @classmethod
    def _opts(cls, v: dict[str, Any]) -> dict[str, Any]:
        return check_options(v)


class TestConnectionResult(BaseModel):
    ok: bool
    message: str
    #: normalized failure class (AUTHENTICATION_FAILED, TLS_ERROR, DATABASE_NOT_FOUND, …)
    error_code: str | None = None
    engine: str | None = None
    version: str | None = None
    latency_ms: float | None = None
    server_info: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class EnvRef(Ref):
    tier: str


class ServerOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    engine: str
    engine_label: str
    version: str | None
    host: str
    port: int
    default_database: str | None
    deployment: str
    cloud_provider: str | None
    environment: EnvRef | None
    site: Ref | None
    cluster: Ref | None
    cluster_role: str | None
    application: Ref | None
    owner: str | None
    business_service: str | None
    tags: list[Tag]
    tls_mode: str
    monitoring_enabled: bool
    collection_interval_s: int
    licensed_features: list[str]
    is_demo: bool  # true for simulated servers (kept for compatibility; see data_source)
    data_source: str = "real"  # real | simulated
    health_status: str
    health_reasons: list[str]
    connection_status: str
    last_error: str | None
    last_connected_at: datetime | None
    last_collected_at: datetime | None
    circuit_open_until: datetime | None
    key_metrics: dict[str, float | None]
    database_count: int | None = None
    open_recommendations: int | None = None
    open_findings: int | None = None
    implementation: str  # implemented|planned|simulated
    management_mode: str = "administration"
    connection_options: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ServerDetailOut(ServerOut):
    capabilities: dict[str, Any]
    credentials: dict[str, Any]
    server_info: dict[str, Any] | None
    unavailable_metrics: dict[str, str]
    permissions: list[str]
    # simulated servers only: profile, seed, planned condition and its explanation
    simulation: dict[str, Any] | None = None
    #: last result per collection component (performance, query_statistics, inventory, …)
    collection_status: dict[str, Any] = Field(default_factory=dict)


class EngineOut(BaseModel):
    engine: str
    display_name: str
    default_port: int
    driver: str
    implementation_phase: int
    implemented: bool
    support_level: str = "planned"
    support_note: str = ""
    driver_status: str | None = None
    driver_detail: dict[str, Any] | None = None
    connection_options: list[dict[str, Any]] = Field(default_factory=list)
    licensable_features: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: dict[str, Any]
