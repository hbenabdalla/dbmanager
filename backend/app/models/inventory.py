"""Fleet inventory: organization, environments, sites, clusters, servers, credentials."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk, utcnow


class DataSource:
    """Where an inventory record comes from.

    * ``real``      - registered or created by people (the default)
    * ``simulated`` - created by the simulation feature; removable as a unit
    * ``system``    - reference data created by the platform itself (bootstrap)
    """

    REAL = "real"
    SIMULATED = "simulated"
    SYSTEM = "system"
    ALL = (REAL, SIMULATED, SYSTEM)


def _data_source_column() -> Mapped[str]:
    return mapped_column(String(16), default=DataSource.REAL, server_default=DataSource.REAL, nullable=False)


def _data_source_check(table: str) -> CheckConstraint:
    return CheckConstraint("data_source IN ('real','simulated','system')", name=f"{table}_data_source")


class Organization(UUIDPk, Timestamps, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)


class Environment(UUIDPk, Timestamps, Base):
    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_environment_name"),
        _data_source_check("environments"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    data_source: Mapped[str] = _data_source_column()
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # production|dr|uat|development|test
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, default=100, nullable=False)


class Site(UUIDPk, Timestamps, Base):
    __tablename__ = "sites"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_site_name"), _data_source_check("sites"))

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    data_source: Mapped[str] = _data_source_column()
    region: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))  # on-prem|aws|azure|gcp|oci|ibm


class Cluster(UUIDPk, Timestamps, Base):
    __tablename__ = "clusters"
    __table_args__ = (_data_source_check("clusters"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    data_source: Mapped[str] = _data_source_column()
    kind: Mapped[str] = mapped_column(String(32), default="primary-replica", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Application(UUIDPk, Timestamps, Base):
    __tablename__ = "applications"
    __table_args__ = (_data_source_check("applications"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    data_source: Mapped[str] = _data_source_column()
    business_service: Mapped[str | None] = mapped_column(String(128))
    owner: Mapped[str | None] = mapped_column(String(128))


class Server(UUIDPk, Timestamps, Base):
    """A registered database server / instance / managed service endpoint."""

    __tablename__ = "servers"
    __table_args__ = (
        Index("ix_servers_env", "environment_id"),
        Index("ix_servers_health", "health_status"),
        Index("ix_servers_data_source", "data_source"),
        _data_source_check("servers"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    engine: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str | None] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    default_database: Mapped[str | None] = mapped_column(String(128))
    deployment: Mapped[str] = mapped_column(String(32), default="self-managed", nullable=False)
    cloud_provider: Mapped[str | None] = mapped_column(String(32))
    cloud_resource_id: Mapped[str | None] = mapped_column(String(512))

    environment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("environments.id", ondelete="SET NULL"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clusters.id", ondelete="SET NULL"))
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"))
    cluster_role: Mapped[str | None] = mapped_column(String(32))  # primary|replica|standby|node
    owner: Mapped[str | None] = mapped_column(String(128))
    business_service: Mapped[str | None] = mapped_column(String(128))

    tls_mode: Mapped[str] = mapped_column(String(16), default="prefer", nullable=False)
    tls_ca_pem: Mapped[str | None] = mapped_column(Text)

    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    collection_interval_s: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    licensed_features: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    # real | simulated (see DataSource). Simulated servers are served exclusively by the
    # simulation adapter: no network connection, no credentials, no SQL execution.
    data_source: Mapped[str] = _data_source_column()

    # --- runtime state ----------------------------------------------------------------
    health_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    health_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    connection_status: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    latest_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    collector_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    environment: Mapped[Environment | None] = relationship(lazy="joined")
    site: Mapped[Site | None] = relationship(lazy="joined")
    cluster: Mapped[Cluster | None] = relationship(lazy="joined")
    application: Mapped[Application | None] = relationship(lazy="joined")
    tags: Mapped[list[ServerTag]] = relationship(lazy="selectin", cascade="all, delete-orphan")

    @hybrid_property
    def is_simulated(self) -> bool:
        return self.data_source == DataSource.SIMULATED

    @is_simulated.inplace.expression
    @classmethod
    def _is_simulated_expression(cls):  # type: ignore[no-untyped-def]
        return cls.data_source == DataSource.SIMULATED

    # Backwards-compatible name used across the API ("is_demo" in JSON = simulated).
    @hybrid_property
    def is_demo(self) -> bool:
        return self.data_source == DataSource.SIMULATED

    @is_demo.inplace.expression
    @classmethod
    def _is_demo_expression(cls):  # type: ignore[no-untyped-def]
        return cls.data_source == DataSource.SIMULATED


class ServerTag(Base):
    __tablename__ = "server_tags"

    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256), default="", nullable=False)


class DatabaseEntry(UUIDPk, Base):
    """Discovered database inventory (cache of the last discovery run)."""

    __tablename__ = "database_entries"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_database_entry"),)

    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    owner: Mapped[str | None] = mapped_column(String(256))
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Credential(UUIDPk, Timestamps, Base):
    """Target database credential. The secret is never stored in plaintext."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("server_id", "purpose", name="uq_credential_purpose"),)

    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # monitoring|readonly|admin
    username: Mapped[str] = mapped_column(String(256), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), default="local", nullable=False)  # local|env|file|vault
    secret_ref: Mapped[str | None] = mapped_column(String(512))  # env var / file path / vault path
    encrypted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # EncryptedBlob for provider=local
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(128))


class DbAccount(UUIDPk, Base):
    """Snapshot of accounts/roles on a target, refreshed by the security scan job.

    Enables fleet-wide questions ("who is admin anywhere?") without connecting live to
    every server.
    """

    __tablename__ = "db_accounts"
    __table_args__ = (UniqueConstraint("server_id", "name", name="uq_db_account"),)

    server_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # user|role
    can_login: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    admin_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    member_of: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    direct_grant_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
