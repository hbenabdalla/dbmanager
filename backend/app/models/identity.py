"""Application users, authentication identities, sessions and RBAC."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPk, utcnow


class User(UUIDPk, Timestamps, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    auth_source: Mapped[str] = mapped_column(String(16), default="local", nullable=False)  # local|ldap|oidc
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    groups: Mapped[list[Group]] = relationship(secondary="group_members", back_populates="members", lazy="selectin")


class Identity(UUIDPk, Base):
    """External identity linked to a user (LDAP DN, OIDC subject)."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider_id", "subject", name="uq_identities_provider_subject"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    last_groups: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    auth_source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))


class Group(UUIDPk, Timestamps, Base):
    __tablename__ = "groups"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    members: Mapped[list[User]] = relationship(secondary="group_members", back_populates="groups", lazy="selectin")


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # manual|ldap|oidc


class Role(UUIDPk, Timestamps, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class RoleAssignment(UUIDPk, Base):
    __tablename__ = "role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "principal_type", "principal_id", "role_id", "scope_type", "scope_id", name="uq_role_assignment"
        ),
        Index("ix_role_assignments_principal", "principal_type", "principal_id"),
    )

    principal_type: Mapped[str] = mapped_column(String(8), nullable=False)  # user|group
    principal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # platform -> "*"; environment/site/cluster/server -> UUID; database -> "<server>/<db>";
    # schema -> "<server>/<db>/<schema>"
    scope_id: Mapped[str] = mapped_column(String(512), nullable=False, default="*")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    role: Mapped[Role] = relationship(lazy="joined")


class IdentityProvider(UUIDPk, Timestamps, Base):
    __tablename__ = "identity_providers"

    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # ldap|oidc|saml
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    secret: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # EncryptedBlob of bind password / client secret
    default_role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("roles.id", ondelete="SET NULL"))


class GroupMapping(UUIDPk, Base):
    __tablename__ = "group_mappings"
    __table_args__ = (UniqueConstraint("provider_id", "external_group", "group_id", name="uq_group_mapping"),)

    provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity_providers.id", ondelete="CASCADE"), nullable=False
    )
    external_group: Mapped[str] = mapped_column(String(512), nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)


class ApiToken(UUIDPk, Base):
    """Personal API token. Only a SHA-256 hash of the secret is stored; the full token
    (``dbm_<prefix>_<secret>``) is shown once at creation."""

    __tablename__ = "api_tokens"
    __table_args__ = (Index("ix_api_tokens_user", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: None = all permissions the user holds; otherwise a subset (intersected per request)
    scopes: Mapped[list[str] | None] = mapped_column(JSONB)
    allowed_cidrs: Mapped[list[str] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(128))
