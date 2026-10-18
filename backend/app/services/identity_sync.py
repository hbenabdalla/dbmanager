"""Shared logic for external identities (LDAP / OIDC): user provisioning + group sync."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GroupMapping, GroupMember, Identity, IdentityProvider, User


async def provision_external_user(
    db: AsyncSession,
    provider: IdentityProvider,
    *,
    subject: str,
    username: str,
    email: str | None,
    display_name: str | None,
    external_groups: list[str],
) -> User | None:
    ident = (
        await db.execute(select(Identity).where(Identity.provider_id == provider.id, Identity.subject == subject))
    ).scalar_one_or_none()
    user: User | None = None
    if ident:
        user = await db.get(User, ident.user_id)
    if user is None:
        user = (
            await db.execute(select(User).where(func.lower(User.username) == username.lower()))
        ).scalar_one_or_none()
        if user is not None and user.auth_source == "local":
            # never silently merge an external identity into a local account
            return None
    if user is None:
        if not provider.config.get("auto_provision", True):
            return None
        user = User(username=username, email=email, display_name=display_name, auth_source=provider.kind)
        db.add(user)
        await db.flush()
    if not user.is_active:
        return None
    user.email = email or user.email
    user.display_name = display_name or user.display_name
    if ident is None:
        ident = Identity(user_id=user.id, provider_id=provider.id, subject=subject)
        db.add(ident)
    ident.last_groups = external_groups[:500]
    await sync_groups(db, provider, user, external_groups)
    await db.flush()
    return user


async def sync_groups(db: AsyncSession, provider: IdentityProvider, user: User, external_groups: list[str]) -> None:
    mappings = (await db.execute(select(GroupMapping).where(GroupMapping.provider_id == provider.id))).scalars().all()
    normalized = {g.lower() for g in external_groups}
    # match full DN or the CN component
    for g in list(normalized):
        if g.startswith("cn="):
            normalized.add(g.split(",")[0][3:])
    wanted = {m.group_id for m in mappings if m.external_group.lower() in normalized}
    default_group = provider.config.get("default_group_id")
    if default_group:
        import uuid

        try:
            wanted.add(uuid.UUID(default_group))
        except ValueError:
            pass
    source = provider.kind
    current = (
        (await db.execute(select(GroupMember).where(GroupMember.user_id == user.id, GroupMember.source == source)))
        .scalars()
        .all()
    )
    current_ids = {m.group_id for m in current}
    for gid in wanted - current_ids:
        exists = await db.get(GroupMember, (gid, user.id))
        if exists is None:
            db.add(GroupMember(group_id=gid, user_id=user.id, source=source))
    stale = current_ids - wanted
    if stale:
        await db.execute(
            delete(GroupMember).where(
                GroupMember.user_id == user.id, GroupMember.source == source, GroupMember.group_id.in_(stale)
            )
        )
