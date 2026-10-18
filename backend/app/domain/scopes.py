"""Authorization scopes and resource references.

Scope hierarchy: platform ⊃ environment ⊃ site ⊃ cluster ⊃ server ⊃ database ⊃ schema.

Environment, site and cluster are *orthogonal attributes* of a server rather than strict
nesting levels, so containment is evaluated by attribute: an environment-scoped
assignment covers every resource whose server belongs to that environment, and so on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ScopeType(StrEnum):
    PLATFORM = "platform"
    ENVIRONMENT = "environment"
    SITE = "site"
    CLUSTER = "cluster"
    SERVER = "server"
    DATABASE = "database"
    SCHEMA = "schema"


SCOPE_DEPTH = {
    ScopeType.PLATFORM: 0,
    ScopeType.ENVIRONMENT: 1,
    ScopeType.SITE: 1,
    ScopeType.CLUSTER: 2,
    ScopeType.SERVER: 3,
    ScopeType.DATABASE: 4,
    ScopeType.SCHEMA: 5,
}


@dataclass(frozen=True)
class ResourceRef:
    """The thing being acted on. ``None`` server means a platform-level resource."""

    server_id: str | None = None
    environment_id: str | None = None
    site_id: str | None = None
    cluster_id: str | None = None
    database: str | None = None
    schema: str | None = None

    @classmethod
    def platform(cls) -> ResourceRef:
        return cls()


@dataclass(frozen=True)
class Scope:
    type: ScopeType
    id: str = "*"

    def contains(self, r: ResourceRef) -> bool:
        """True when the scope covers the resource (assignment grants access to it)."""
        t = self.type
        if t is ScopeType.PLATFORM:
            return True
        if r.server_id is None:  # platform resource: only platform scope covers it
            return False
        if t is ScopeType.ENVIRONMENT:
            return r.environment_id == self.id
        if t is ScopeType.SITE:
            return r.site_id == self.id
        if t is ScopeType.CLUSTER:
            return r.cluster_id == self.id
        if t is ScopeType.SERVER:
            return r.server_id == self.id
        if t is ScopeType.DATABASE:
            return r.database is not None and f"{r.server_id}/{r.database}" == self.id
        if t is ScopeType.SCHEMA:
            return (
                r.database is not None and r.schema is not None and f"{r.server_id}/{r.database}/{r.schema}" == self.id
            )
        return False

    def touches(self, r: ResourceRef) -> bool:
        """True when the scope covers the resource *or something inside it*.

        Used for visibility (e.g. a database-scoped assignment makes its server visible in
        lists, without granting server-level actions).
        """
        if self.contains(r):
            return True
        if r.server_id is None:
            return self.type is not ScopeType.PLATFORM  # any assignment reveals "some" platform content
        if self.type in (ScopeType.DATABASE, ScopeType.SCHEMA):
            parts = self.id.split("/")
            if parts[0] != r.server_id:
                return False
            if r.database is None:
                return True
            return len(parts) >= 2 and parts[1] == r.database and (r.schema is None)
        return False

    def is_within(self, outer: Scope) -> bool:
        """Is this scope equal to or narrower than ``outer``? (for anti-escalation checks).

        Attribute scopes (environment/site/cluster) can only be compared with platform or
        themselves without resolving servers, so this is conservative.
        """
        if outer.type is ScopeType.PLATFORM:
            return True
        if outer == self:
            return True
        if outer.type is ScopeType.SERVER and self.type in (ScopeType.DATABASE, ScopeType.SCHEMA):
            return self.id.split("/")[0] == outer.id
        if outer.type is ScopeType.DATABASE and self.type is ScopeType.SCHEMA:
            return "/".join(self.id.split("/")[:2]) == outer.id
        return False
