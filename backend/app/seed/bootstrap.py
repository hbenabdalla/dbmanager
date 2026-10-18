"""Idempotent bootstrap: default organization, environments, system roles, first admin.

This is the only data a new installation gets. No simulated fleet is created here; demo data
is generated on request by a Platform Administrator (see :mod:`app.simulation`).
"""

from __future__ import annotations

import logging
import secrets as pysecrets

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, password_policy_errors
from app.domain.permissions import SYSTEM_ROLES
from app.models import DataSource, Environment, Organization, Role, RoleAssignment, User

log = logging.getLogger("app.bootstrap")

DEFAULT_ENVIRONMENTS = [
    ("Production", "production", 10),
    ("DR", "dr", 20),
    ("UAT", "uat", 30),
    ("Development", "development", 40),
    ("Test", "test", 50),
]


async def default_org(db: AsyncSession) -> Organization:
    org = (await db.execute(select(Organization).order_by(Organization.created_at).limit(1))).scalar_one_or_none()
    if org is None:
        org = Organization(name="Default organization", slug="default")
        db.add(org)
        await db.flush()
    return org


async def ensure_environments(db: AsyncSession, org: Organization) -> None:
    existing = {e.name for e in (await db.execute(select(Environment))).scalars()}
    for name, tier, order in DEFAULT_ENVIRONMENTS:
        if name not in existing:
            db.add(
                Environment(
                    organization_id=org.id, name=name, tier=tier, sort_order=order, data_source=DataSource.SYSTEM
                )
            )


async def sync_system_roles(db: AsyncSession) -> dict[str, Role]:
    roles = {r.name: r for r in (await db.execute(select(Role))).scalars()}
    for name, (desc, perms) in SYSTEM_ROLES.items():
        r = roles.get(name)
        if r is None:
            r = Role(name=name, description=desc, is_system=True, permissions=sorted(perms))
            db.add(r)
            roles[name] = r
        else:
            r.is_system, r.description, r.permissions = True, desc, sorted(perms)
    await db.flush()
    return roles


async def ensure_admin(db: AsyncSession, roles: dict[str, Role]) -> None:
    s = get_settings()
    count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    if count:
        return
    password = s.bootstrap_admin_password.get_secret_value()
    generated = False
    if not password:
        if not s.is_dev:
            log.error(
                "No users exist and DBM_BOOTSTRAP_ADMIN_PASSWORD is not set; refusing to create an admin "
                "with a generated password outside development. Set the variable and restart."
            )
            return
        password = pysecrets.token_urlsafe(18) + "aA1!"
        generated = True
    errs = password_policy_errors(password)
    if errs:
        log.error("DBM_BOOTSTRAP_ADMIN_PASSWORD does not meet the password policy: %s", "; ".join(errs))
        return
    admin = User(
        username=s.bootstrap_admin_username,
        email=s.bootstrap_admin_email,
        display_name="Platform administrator",
        auth_source="local",
        password_hash=hash_password(password),
        must_change_password=generated,
    )
    db.add(admin)
    await db.flush()
    db.add(
        RoleAssignment(
            principal_type="user",
            principal_id=admin.id,
            role_id=roles["Platform Administrator"].id,
            scope_type="platform",
            scope_id="*",
        )
    )
    if generated:
        # Development only: printed once to the console so a fresh dev stack is usable.
        log.warning(
            "DEVELOPMENT BOOTSTRAP: created user %r with generated password %s — change it after login",
            admin.username,
            password,
        )
    else:
        log.info("bootstrap administrator %r created", admin.username)


async def bootstrap(db: AsyncSession) -> None:
    org = await default_org(db)
    await ensure_environments(db, org)
    roles = await sync_system_roles(db)
    await ensure_admin(db, roles)
    await db.flush()
