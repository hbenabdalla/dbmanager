"""Performance recommendations for simulated servers.

No recommendation text is invented here: the platform's own rule engine evaluates the
simulated evidence (metrics, query statistics, configuration), exactly as for real servers,
so every recommendation cites the generated values that triggered it.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Server
from app.services import advisor


async def generate(db: AsyncSession, server: Server) -> dict[str, Any]:
    return await advisor.run_performance(db, server)
