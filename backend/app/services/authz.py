"""Backend authorization: scoped, capability-based RBAC.

Every sensitive operation calls :meth:`Authorizer.require` with a permission and the
resource it touches. The frontend only *hides* things; this module *enforces* them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDenied
from app.domain.permissions import ALL_PERMISSIONS
from app.domain.scopes import ResourceRef, Scope, ScopeType
from app.models import GroupMember, Role, RoleAssignment, Server

if TYPE_CHECKING:
    from app.models import User


@dataclass(frozen=True)
class Grant:
    scope: Scope
    permissions: frozenset[str]
    role_name: str
    via_group: str | None = None


@dataclass
class Authorizer:
    user_id: uuid.UUID
    grants: list[Grant] = field(default_factory=list)

    # ---------------------------------------------------------------------------------
    @classmethod
    async def load(cls, db: AsyncSession, user: User) -> Authorizer:
        group_rows = (
            (await db.execute(select(GroupMember.group_id).where(GroupMember.user_id == user.id))).scalars().all()
        )
        principals = [("user", user.id)] + [("group", g) for g in group_rows]
        conds = [(RoleAssignment.principal_type == pt) & (RoleAssignment.principal_id == pid) for pt, pid in principals]
        rows = (await db.execute(select(RoleAssignment, Role).join(Role).where(or_(*conds)))).all()
        grants = [
            Grant(
                scope=Scope(ScopeType(a.scope_type), a.scope_id),
                permissions=frozenset(p for p in r.permissions if p in ALL_PERMISSIONS),
                role_name=r.name,
                via_group=str(a.principal_id) if a.principal_type == "group" else None,
            )
            for a, r in rows
        ]
        return cls(user_id=user.id, grants=grants)

    # ---------------------------------------------------------------------------------
    def has(self, permission: str, resource: ResourceRef | None = None) -> bool:
        res = resource or ResourceRef.platform()
        return any(permission in g.permissions and g.scope.contains(res) for g in self.grants)

    def has_anywhere(self, permission: str) -> bool:
        return any(permission in g.permissions for g in self.grants)

    def can_see(self, permission: str, resource: ResourceRef) -> bool:
        """Visibility: the user holds ``permission`` on the resource or anything inside it."""
        return any(permission in g.permissions and g.scope.touches(resource) for g in self.grants)

    def require(self, permission: str, resource: ResourceRef | None = None) -> None:
        if not self.has(permission, resource):
            raise PermissionDenied(
                f"Missing permission '{permission}' for this resource",
                details={"permission": permission},
            )

    def require_anywhere(self, permission: str) -> None:
        if not self.has_anywhere(permission):
            raise PermissionDenied(f"Missing permission '{permission}'", details={"permission": permission})

    def permissions_for(self, resource: ResourceRef | None = None) -> set[str]:
        res = resource or ResourceRef.platform()
        out: set[str] = set()
        for g in self.grants:
            if g.scope.contains(res):
                out |= g.permissions
        return out

    def all_permissions(self) -> set[str]:
        out: set[str] = set()
        for g in self.grants:
            out |= g.permissions
        return out

    @property
    def is_platform_admin(self) -> bool:
        return self.has("rbac.manage") and self.has("users.manage")

    # ---------------------------------------------------------------------------------
    def server_filter(self, permission: str = "server.view") -> ColumnElement[bool] | None:
        """SQL predicate restricting ``Server`` rows to those visible for ``permission``.

        Returns ``None`` when the user can see every server (platform scope).
        """
        envs: set[str] = set()
        sites: set[str] = set()
        clusters: set[str] = set()
        servers: set[str] = set()
        for g in self.grants:
            if permission not in g.permissions:
                continue
            t = g.scope.type
            if t is ScopeType.PLATFORM:
                return None
            if t is ScopeType.ENVIRONMENT:
                envs.add(g.scope.id)
            elif t is ScopeType.SITE:
                sites.add(g.scope.id)
            elif t is ScopeType.CLUSTER:
                clusters.add(g.scope.id)
            else:
                servers.add(g.scope.id.split("/")[0])
        conds: list[ColumnElement[bool]] = []

        def _uuids(values: set[str]) -> list[uuid.UUID]:
            out = []
            for v in values:
                try:
                    out.append(uuid.UUID(v))
                except ValueError:
                    continue
            return out

        if envs:
            conds.append(Server.environment_id.in_(_uuids(envs)))
        if sites:
            conds.append(Server.site_id.in_(_uuids(sites)))
        if clusters:
            conds.append(Server.cluster_id.in_(_uuids(clusters)))
        if servers:
            conds.append(Server.id.in_(_uuids(servers)))
        return or_(*conds) if conds else false()

    # ---------------------------------------------------------------------------------
    def can_delegate(self, role_permissions: set[str], target: ResourceRef) -> list[str]:
        """Anti-escalation: return permissions the actor would hand out without holding them."""
        held = self.permissions_for(target)
        return sorted(role_permissions - held)


def server_ref(server: Server, database: str | None = None, schema: str | None = None) -> ResourceRef:
    return ResourceRef(
        server_id=str(server.id),
        environment_id=str(server.environment_id) if server.environment_id else None,
        site_id=str(server.site_id) if server.site_id else None,
        cluster_id=str(server.cluster_id) if server.cluster_id else None,
        database=database,
        schema=schema,
    )


async def scope_ref(db: AsyncSession, scope: Scope) -> ResourceRef:
    """A representative resource for a scope (used to evaluate delegation)."""
    if scope.type is ScopeType.PLATFORM:
        return ResourceRef.platform()
    if scope.type is ScopeType.ENVIRONMENT:
        return ResourceRef(server_id="*", environment_id=scope.id)
    if scope.type is ScopeType.SITE:
        return ResourceRef(server_id="*", site_id=scope.id)
    if scope.type is ScopeType.CLUSTER:
        return ResourceRef(server_id="*", cluster_id=scope.id)
    parts = scope.id.split("/")
    try:
        sid = uuid.UUID(parts[0])
    except ValueError:
        return ResourceRef(server_id=parts[0])
    server = await db.get(Server, sid)
    if server is None:
        return ResourceRef(server_id=parts[0])
    return server_ref(
        server,
        database=parts[1] if len(parts) > 1 else None,
        schema=parts[2] if len(parts) > 2 else None,
    )
