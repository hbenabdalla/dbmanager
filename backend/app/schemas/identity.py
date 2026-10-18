from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORM


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=1, max_length=512)


class GrantOut(BaseModel):
    role: str
    scope_type: str
    scope_id: str
    scope_label: str | None = None
    via_group: str | None = None


class MeOut(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None
    email: str | None
    auth_source: str
    must_change_password: bool
    permissions: list[str]  # union of all permissions anywhere (UI hint only)
    platform_permissions: list[str]  # permissions held at platform scope
    grants: list[GrantOut]
    csrf_token: str | None = None
    #: TOTP enrolled for this (local) account
    mfa_enabled: bool = False
    #: the MFA policy requires this account to enroll before it can do anything else
    mfa_enrollment_required: bool = False


class MfaChallenge(BaseModel):
    """First sign-in step succeeded; send the code with ``mfa_token`` to /auth/login/mfa."""

    mfa_required: bool = True
    mfa_token: str
    methods: list[str] = ["totp", "recovery"]


class MfaLoginRequest(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=4096)
    code: str = Field(min_length=6, max_length=32)


class ProviderPublic(BaseModel):
    id: uuid.UUID
    kind: str
    name: str


class UserOut(ORM):
    id: uuid.UUID
    username: str
    email: str | None
    display_name: str | None
    auth_source: str
    is_active: bool
    must_change_password: bool
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    groups: list[GroupRef] = Field(default_factory=list)


class GroupRef(ORM):
    id: uuid.UUID
    name: str


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z0-9._@\-]+$")
    email: EmailStr | None = None
    display_name: str | None = Field(None, max_length=255)
    password: str = Field(min_length=1, max_length=512)
    must_change_password: bool = True


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    display_name: str | None = Field(None, max_length=255)
    is_active: bool | None = None
    unlock: bool | None = None


class ResetPassword(BaseModel):
    new_password: str = Field(min_length=1, max_length=512)
    must_change_password: bool = True


class GroupOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    members: list[GroupMemberOut] = Field(default_factory=list)


class GroupMemberOut(ORM):
    id: uuid.UUID
    username: str
    display_name: str | None


class GroupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None


class GroupMembers(BaseModel):
    user_ids: list[uuid.UUID]


class RoleOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    is_system: bool
    permissions: list[str]


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    permissions: list[str]


class RoleUpdate(BaseModel):
    name: str | None = Field(None, min_length=2, max_length=128)
    description: str | None = None
    permissions: list[str] | None = None


ScopeTypeLiteral = Literal["platform", "environment", "site", "cluster", "server", "database", "schema"]


class AssignmentCreate(BaseModel):
    principal_type: Literal["user", "group"]
    principal_id: uuid.UUID
    role_id: uuid.UUID
    scope_type: ScopeTypeLiteral
    scope_id: str = "*"

    @field_validator("scope_id")
    @classmethod
    def _scope(cls, v: str) -> str:
        if len(v) > 512:
            raise ValueError("scope id too long")
        return v


class AssignmentOut(BaseModel):
    id: uuid.UUID
    principal_type: str
    principal_id: uuid.UUID
    principal_name: str | None
    role_id: uuid.UUID
    role_name: str
    scope_type: str
    scope_id: str
    scope_label: str
    created_at: datetime


class PermissionOut(BaseModel):
    key: str
    group: str
    description: str
    platform_only: bool


class IdentityProviderOut(ORM):
    id: uuid.UUID
    kind: str
    name: str
    enabled: bool
    config: dict[str, Any]
    has_secret: bool = False
    mappings: list[GroupMappingOut] = Field(default_factory=list)


class GroupMappingOut(ORM):
    id: uuid.UUID
    external_group: str
    group_id: uuid.UUID


class IdentityProviderIn(BaseModel):
    kind: Literal["ldap", "oidc", "saml"]
    name: str = Field(min_length=2, max_length=128)
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = Field(None, description="LDAP bind password or OIDC client secret (write-only)")


class IdentityProviderUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None
    secret: str | None = None


class GroupMappingIn(BaseModel):
    external_group: str = Field(min_length=1, max_length=512)
    group_id: uuid.UUID


UserOut.model_rebuild()
GroupOut.model_rebuild()
IdentityProviderOut.model_rebuild()
