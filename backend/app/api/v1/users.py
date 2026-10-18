from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import delete, func, or_, select

from app.api.deps import DB, CurrentPrincipal
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import hash_password
from app.models import Group, GroupMember, RoleAssignment, User
from app.schemas.common import Message, Page, PageParams, apply_sort, paginate
from app.schemas.identity import (
    GroupCreate,
    GroupMembers,
    GroupOut,
    ResetPassword,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.services import audit, auth
from app.services.authz import Authorizer

router = APIRouter(tags=["users"])


@router.get("/users", response_model=Page[UserOut])
async def list_users(db: DB, p: CurrentPrincipal, params: PageParams = Depends()) -> Page[UserOut]:
    if not (p.authz.has("users.manage") or p.authz.has_anywhere("rbac.manage")):
        p.authz.require("users.manage")
    stmt = select(User)
    if params.q:
        like = f"%{params.q.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(User.username).like(like),
                func.lower(User.display_name).like(like),
                func.lower(User.email).like(like),
            )
        )
    stmt = apply_sort(
        stmt,
        params.sort,
        {"username": User.username, "last_login_at": User.last_login_at, "created_at": User.created_at},
        User.username,
    )
    items, total = await paginate(db, stmt, params.page, params.page_size)
    return Page(
        items=[UserOut.model_validate(u) for u in items], total=total, page=params.page, page_size=params.page_size
    )


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, db: DB, p: CurrentPrincipal) -> UserOut:
    p.authz.require("users.manage")
    if (await db.execute(select(User).where(func.lower(User.username) == body.username.lower()))).scalar_one_or_none():
        raise Conflict("A user with this username already exists")
    auth.validate_new_password(body.password)
    u = User(
        username=body.username,
        email=body.email,
        display_name=body.display_name,
        auth_source="local",
        password_hash=hash_password(body.password),
        must_change_password=body.must_change_password,
    )
    db.add(u)
    await db.flush()
    await db.refresh(u, ["groups"])
    await audit.record("user.create", actor=p, target_type="user", target_id=str(u.id), object_name=u.username)
    return UserOut.model_validate(u)


async def _user(db: DB, user_id: uuid.UUID) -> User:
    u = await db.get(User, user_id)
    if u is None:
        raise NotFound("User not found")
    return u


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> UserOut:
    if p.user.id != user_id and not (p.authz.has("users.manage") or p.authz.has_anywhere("rbac.manage")):
        p.authz.require("users.manage")
    return UserOut.model_validate(await _user(db, user_id))


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: DB, p: CurrentPrincipal) -> UserOut:
    p.authz.require("users.manage")
    u = await _user(db, user_id)
    if body.is_active is False and u.id == p.user.id:
        raise ValidationFailed("You cannot deactivate your own account")
    changes = body.model_dump(exclude_unset=True)
    for k in ("email", "display_name", "is_active"):
        if k in changes:
            setattr(u, k, changes[k])
    if body.unlock:
        u.failed_logins, u.locked_until = 0, None
    if body.is_active is False:
        await auth.revoke_user_sessions(db, u.id)
    await audit.record(
        "user.update",
        actor=p,
        target_type="user",
        target_id=str(u.id),
        object_name=u.username,
        details={"changes": changes},
    )
    return UserOut.model_validate(u)


@router.delete("/users/{user_id}", response_model=Message)
async def delete_user(user_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("users.manage")
    u = await _user(db, user_id)
    if u.id == p.user.id:
        raise ValidationFailed("You cannot delete your own account")
    await db.execute(
        delete(RoleAssignment).where(RoleAssignment.principal_type == "user", RoleAssignment.principal_id == u.id)
    )
    name = u.username
    await db.delete(u)
    await audit.record(
        "user.delete", actor=p, target_type="user", target_id=str(user_id), object_name=name, risk_class="DESTRUCTIVE"
    )
    return Message(message=f"User {name} deleted")


@router.post("/users/{user_id}/reset-password", response_model=Message)
async def reset_password(user_id: uuid.UUID, body: ResetPassword, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("users.manage")
    u = await _user(db, user_id)
    if u.auth_source != "local":
        raise ValidationFailed("Directory accounts cannot have a local password")
    auth.validate_new_password(body.new_password)
    u.password_hash = hash_password(body.new_password)
    u.must_change_password = body.must_change_password
    u.failed_logins, u.locked_until = 0, None
    await auth.revoke_user_sessions(db, u.id)
    await audit.record("user.reset_password", actor=p, target_type="user", target_id=str(u.id), object_name=u.username)
    return Message(message="Password reset; existing sessions were signed out")


@router.get("/users/{user_id}/effective-permissions")
async def effective_permissions(user_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> dict[str, object]:
    if p.user.id != user_id and not (p.authz.has("users.manage") or p.authz.has_anywhere("rbac.manage")):
        p.authz.require("users.manage")
    u = await _user(db, user_id)
    az = await Authorizer.load(db, u)
    from app.services.labels import scope_labels

    labels = await scope_labels(db, [(g.scope.type.value, g.scope.id) for g in az.grants])
    return {
        "user": u.username,
        "grants": [
            {
                "role": g.role_name,
                "scope_type": g.scope.type.value,
                "scope_id": g.scope.id,
                "scope_label": labels.get((g.scope.type.value, g.scope.id)),
                "via_group": g.via_group,
                "permissions": sorted(g.permissions),
            }
            for g in az.grants
        ],
    }


# ---- groups ---------------------------------------------------------------------------
@router.get("/groups", response_model=list[GroupOut])
async def list_groups(db: DB, p: CurrentPrincipal) -> list[GroupOut]:
    if not (p.authz.has("users.manage") or p.authz.has_anywhere("rbac.manage")):
        p.authz.require("users.manage")
    rows = (await db.execute(select(Group).order_by(Group.name))).scalars().all()
    return [GroupOut.model_validate(g) for g in rows]


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(body: GroupCreate, db: DB, p: CurrentPrincipal) -> GroupOut:
    p.authz.require("users.manage")
    if (await db.execute(select(Group).where(Group.name == body.name))).scalar_one_or_none():
        raise Conflict("A group with this name already exists")
    g = Group(name=body.name, description=body.description)
    db.add(g)
    await db.flush()
    await db.refresh(g, ["members"])
    await audit.record("group.create", actor=p, target_type="group", target_id=str(g.id), object_name=g.name)
    return GroupOut.model_validate(g)


@router.put("/groups/{group_id}/members", response_model=GroupOut)
async def set_members(group_id: uuid.UUID, body: GroupMembers, db: DB, p: CurrentPrincipal) -> GroupOut:
    p.authz.require("users.manage")
    g = await db.get(Group, group_id)
    if g is None:
        raise NotFound("Group not found")
    # Group membership confers the group's roles: refuse to add yourself (self-escalation).
    if p.user.id in body.user_ids and not any(m.id == p.user.id for m in g.members):
        raise ValidationFailed("You cannot add yourself to a group")
    await db.execute(delete(GroupMember).where(GroupMember.group_id == g.id, GroupMember.source == "manual"))
    for uid in set(body.user_ids):
        if await db.get(User, uid) is None:
            raise NotFound(f"User {uid} not found")
        if await db.get(GroupMember, (g.id, uid)) is None:
            db.add(GroupMember(group_id=g.id, user_id=uid, source="manual"))
    await db.flush()
    await db.refresh(g, ["members"])
    await audit.record(
        "group.members",
        actor=p,
        target_type="group",
        target_id=str(g.id),
        object_name=g.name,
        details={"user_ids": [str(u) for u in body.user_ids]},
    )
    return GroupOut.model_validate(g)


@router.delete("/groups/{group_id}", response_model=Message)
async def delete_group(group_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("users.manage")
    g = await db.get(Group, group_id)
    if g is None:
        raise NotFound("Group not found")
    await db.execute(
        delete(RoleAssignment).where(RoleAssignment.principal_type == "group", RoleAssignment.principal_id == g.id)
    )
    await db.delete(g)
    await audit.record("group.delete", actor=p, target_type="group", target_id=str(group_id), object_name=g.name)
    return Message(message="Group deleted")
