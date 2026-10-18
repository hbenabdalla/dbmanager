"""Roles, permission catalog and scoped role assignments (with anti-escalation rules)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.domain.permissions import CATALOG, validate_permissions
from app.domain.scopes import Scope, ScopeType
from app.models import Cluster, Environment, Group, Role, RoleAssignment, Server, Site, User
from app.schemas.common import Message
from app.schemas.identity import (
    AssignmentCreate,
    AssignmentOut,
    PermissionOut,
    RoleCreate,
    RoleOut,
    RoleUpdate,
)
from app.services import audit
from app.services.authz import scope_ref
from app.services.labels import scope_labels

router = APIRouter(tags=["rbac"])


@router.get("/permissions", response_model=list[PermissionOut])
async def permissions(p: CurrentPrincipal) -> list[PermissionOut]:
    return [
        PermissionOut(key=x.key, group=x.group, description=x.description, platform_only=x.platform_only)
        for x in CATALOG
    ]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(db: DB, p: CurrentPrincipal) -> list[RoleOut]:
    rows = (await db.execute(select(Role).order_by(Role.is_system.desc(), Role.name))).scalars().all()
    return [RoleOut.model_validate(r) for r in rows]


@router.post("/roles", response_model=RoleOut, status_code=201)
async def create_role(body: RoleCreate, db: DB, p: CurrentPrincipal) -> RoleOut:
    p.authz.require("roles.manage")
    if unknown := validate_permissions(body.permissions):
        raise ValidationFailed("Unknown permissions", details={"unknown": unknown})
    if (await db.execute(select(Role).where(Role.name == body.name))).scalar_one_or_none():
        raise Conflict("A role with this name already exists")
    r = Role(name=body.name, description=body.description, permissions=sorted(set(body.permissions)))
    db.add(r)
    await db.flush()
    await audit.record(
        "role.create",
        actor=p,
        target_type="role",
        target_id=str(r.id),
        object_name=r.name,
        details={"permissions": r.permissions},
    )
    return RoleOut.model_validate(r)


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: uuid.UUID, body: RoleUpdate, db: DB, p: CurrentPrincipal) -> RoleOut:
    p.authz.require("roles.manage")
    r = await db.get(Role, role_id)
    if r is None:
        raise NotFound("Role not found")
    if r.is_system:
        raise ValidationFailed("System roles cannot be modified; create a custom role instead")
    if body.permissions is not None:
        if unknown := validate_permissions(body.permissions):
            raise ValidationFailed("Unknown permissions", details={"unknown": unknown})
        r.permissions = sorted(set(body.permissions))
    if body.name:
        r.name = body.name
    if body.description is not None:
        r.description = body.description
    await audit.record(
        "role.update",
        actor=p,
        target_type="role",
        target_id=str(r.id),
        object_name=r.name,
        details={"permissions": r.permissions},
    )
    return RoleOut.model_validate(r)


@router.delete("/roles/{role_id}", response_model=Message)
async def delete_role(role_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("roles.manage")
    r = await db.get(Role, role_id)
    if r is None:
        raise NotFound("Role not found")
    if r.is_system:
        raise ValidationFailed("System roles cannot be deleted")
    await db.delete(r)
    await audit.record("role.delete", actor=p, target_type="role", target_id=str(role_id), object_name=r.name)
    return Message(message="Role deleted")


# ---- assignments ------------------------------------------------------------------------
async def _validate_scope(db: DB, scope_type: str, scope_id: str) -> None:
    t = ScopeType(scope_type)
    if t is ScopeType.PLATFORM:
        if scope_id != "*":
            raise ValidationFailed("platform scope id must be '*'")
        return
    model = {ScopeType.ENVIRONMENT: Environment, ScopeType.SITE: Site, ScopeType.CLUSTER: Cluster}.get(t, Server)
    parts = scope_id.split("/")
    expected = {ScopeType.DATABASE: 2, ScopeType.SCHEMA: 3}.get(t, 1)
    if len(parts) != expected or not all(parts):
        raise ValidationFailed(f"{t.value} scope id must have {expected} '/'-separated part(s)")
    try:
        oid = uuid.UUID(parts[0])
    except ValueError as exc:
        raise ValidationFailed("scope id must start with a UUID") from exc
    if await db.get(model, oid) is None:
        raise NotFound(f"{model.__name__} for scope not found")


async def _out(db: DB, rows: list[tuple[RoleAssignment, Role]]) -> list[AssignmentOut]:
    labels = await scope_labels(db, [(a.scope_type, a.scope_id) for a, _ in rows])
    users = {
        u.id: u.username
        for u in (
            await db.execute(
                select(User).where(User.id.in_([a.principal_id for a, _ in rows if a.principal_type == "user"]))
            )
        ).scalars()
    }
    groups = {
        g.id: g.name
        for g in (
            await db.execute(
                select(Group).where(Group.id.in_([a.principal_id for a, _ in rows if a.principal_type == "group"]))
            )
        ).scalars()
    }
    return [
        AssignmentOut(
            id=a.id,
            principal_type=a.principal_type,
            principal_id=a.principal_id,
            principal_name=(users if a.principal_type == "user" else groups).get(a.principal_id),
            role_id=r.id,
            role_name=r.name,
            scope_type=a.scope_type,
            scope_id=a.scope_id,
            scope_label=labels.get((a.scope_type, a.scope_id), a.scope_id),
            created_at=a.created_at,
        )
        for a, r in rows
    ]


@router.get("/role-assignments", response_model=list[AssignmentOut])
async def list_assignments(db: DB, p: CurrentPrincipal, principal_id: uuid.UUID | None = None) -> list[AssignmentOut]:
    if not p.authz.has("users.manage"):
        p.authz.require_anywhere("rbac.manage")
    stmt = select(RoleAssignment, Role).join(Role).order_by(RoleAssignment.created_at)
    if principal_id:
        stmt = stmt.where(RoleAssignment.principal_id == principal_id)
    return await _out(db, list((await db.execute(stmt)).all()))


@router.post("/role-assignments", response_model=AssignmentOut, status_code=201)
async def create_assignment(body: AssignmentCreate, db: DB, p: CurrentPrincipal) -> AssignmentOut:
    role = await db.get(Role, body.role_id)
    if role is None:
        raise NotFound("Role not found")
    await _validate_scope(db, body.scope_type, body.scope_id)
    scope = Scope(ScopeType(body.scope_type), body.scope_id)
    target = await scope_ref(db, scope)

    # 1. the actor must be allowed to manage RBAC at (or above) the target scope
    if not p.authz.has("rbac.manage", target):
        await audit.record(
            "rbac.assign",
            actor=p,
            result="denied",
            target_type="role_assignment",
            object_name=role.name,
            failure_reason="rbac.manage not held for target scope",
            details=body.model_dump(mode="json"),
        )
        raise PermissionDenied("You cannot manage role assignments for this scope")
    # 2. nobody can change their own access (also via a group they belong to)
    if body.principal_type == "user" and body.principal_id == p.user.id:
        raise PermissionDenied("You cannot assign roles to yourself")
    if body.principal_type == "group":
        group = await db.get(Group, body.principal_id)
        if group is None:
            raise NotFound("Group not found")
        if any(m.id == p.user.id for m in group.members):
            raise PermissionDenied("You cannot assign roles to a group you belong to")
    elif await db.get(User, body.principal_id) is None:
        raise NotFound("User not found")
    # 3. you cannot hand out permissions you do not hold yourself at that scope
    missing = p.authz.can_delegate(set(role.permissions), target)
    if missing:
        await audit.record(
            "rbac.assign",
            actor=p,
            result="denied",
            target_type="role_assignment",
            object_name=role.name,
            failure_reason="privilege escalation blocked",
            details={"missing": missing, **body.model_dump(mode="json")},
        )
        raise PermissionDenied(
            "The role contains permissions you do not hold for this scope", details={"missing_permissions": missing}
        )

    exists = (
        await db.execute(
            select(RoleAssignment).where(
                RoleAssignment.principal_type == body.principal_type,
                RoleAssignment.principal_id == body.principal_id,
                RoleAssignment.role_id == role.id,
                RoleAssignment.scope_type == body.scope_type,
                RoleAssignment.scope_id == body.scope_id,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise Conflict("This assignment already exists")
    a = RoleAssignment(
        principal_type=body.principal_type,
        principal_id=body.principal_id,
        role_id=role.id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        created_by=p.user.id,
    )
    db.add(a)
    await db.flush()
    await audit.record(
        "rbac.assign",
        actor=p,
        target_type="role_assignment",
        target_id=str(a.id),
        object_name=role.name,
        risk_class="PRIVILEGED_CHANGE",
        details=body.model_dump(mode="json"),
    )
    return (await _out(db, [(a, role)]))[0]


@router.delete("/role-assignments/{assignment_id}", response_model=Message)
async def delete_assignment(assignment_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    a = await db.get(RoleAssignment, assignment_id)
    if a is None:
        raise NotFound("Assignment not found")
    target = await scope_ref(db, Scope(ScopeType(a.scope_type), a.scope_id))
    if not p.authz.has("rbac.manage", target):
        raise PermissionDenied("You cannot manage role assignments for this scope")
    if a.principal_type == "user" and a.principal_id == p.user.id:
        raise PermissionDenied("You cannot remove your own role assignments")
    await db.delete(a)
    await audit.record(
        "rbac.unassign",
        actor=p,
        target_type="role_assignment",
        target_id=str(assignment_id),
        risk_class="PRIVILEGED_CHANGE",
        details={"principal_id": str(a.principal_id), "scope_type": a.scope_type, "scope_id": a.scope_id},
    )
    return Message(message="Assignment removed")
