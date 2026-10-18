import uuid

from app.domain.scopes import ResourceRef, Scope, ScopeType
from app.services.authz import Authorizer, Grant

ENV_PROD, ENV_DEV = str(uuid.uuid4()), str(uuid.uuid4())
SRV_A, SRV_B = str(uuid.uuid4()), str(uuid.uuid4())
A = ResourceRef(server_id=SRV_A, environment_id=ENV_PROD)
B = ResourceRef(server_id=SRV_B, environment_id=ENV_DEV)


def az(*grants: tuple[ScopeType, str, set[str]]) -> Authorizer:
    return Authorizer(uuid.uuid4(), [Grant(Scope(t, i), frozenset(p), "r") for t, i, p in grants])


def test_platform_scope_covers_everything() -> None:
    a = az((ScopeType.PLATFORM, "*", {"server.view"}))
    assert a.has("server.view", A) and a.has("server.view", B) and a.has("server.view")
    assert a.server_filter("server.view") is None


def test_environment_scope() -> None:
    a = az((ScopeType.ENVIRONMENT, ENV_PROD, {"performance.view"}))
    assert a.has("performance.view", A)
    assert not a.has("performance.view", B)
    assert not a.has("performance.view")  # platform resource
    assert not a.has("session.view", A)


def test_database_scope_does_not_grant_server_actions_but_reveals_server() -> None:
    a = az((ScopeType.DATABASE, f"{SRV_A}/orders", {"schema.view", "server.view"}))
    assert a.has("schema.view", ResourceRef(server_id=SRV_A, environment_id=ENV_PROD, database="orders"))
    assert not a.has("schema.view", ResourceRef(server_id=SRV_A, environment_id=ENV_PROD, database="billing"))
    assert not a.has("server.view", A)
    assert a.can_see("server.view", A)


def test_schema_scope() -> None:
    s = Scope(ScopeType.SCHEMA, f"{SRV_A}/orders/sales")
    assert s.contains(ResourceRef(server_id=SRV_A, database="orders", schema="sales"))
    assert not s.contains(ResourceRef(server_id=SRV_A, database="orders", schema="public"))
    assert s.is_within(Scope(ScopeType.SERVER, SRV_A))
    assert s.is_within(Scope(ScopeType.DATABASE, f"{SRV_A}/orders"))


def test_delegation_blocks_escalation() -> None:
    a = az((ScopeType.ENVIRONMENT, ENV_DEV, {"rbac.manage", "server.view", "performance.view"}))
    target = ResourceRef(server_id="*", environment_id=ENV_DEV)
    assert a.can_delegate({"server.view"}, target) == []
    assert a.can_delegate({"server.view", "query.modify"}, target) == ["query.modify"]
    prod = ResourceRef(server_id="*", environment_id=ENV_PROD)
    assert not a.has("rbac.manage", prod)


def test_no_grants_sees_nothing() -> None:
    a = az()
    assert not a.has_anywhere("server.view")
    assert str(a.server_filter()) == "false"
