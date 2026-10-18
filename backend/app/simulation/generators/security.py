"""Security findings for simulated servers.

Findings come from the platform's security rule engine applied to the simulated security
configuration (accounts, grants, encryption, auditing, version) of each server, so they follow
from that server's scenarios: hardened production systems raise few findings, neglected
development systems many.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Server
from app.services import advisor


async def generate(db: AsyncSession, server: Server) -> dict[str, Any]:
    return await advisor.run_security(db, server)
