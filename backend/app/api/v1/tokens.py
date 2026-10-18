"""Personal API tokens (create / list / revoke) and their administration.

These endpoints are interactive-only: they cannot be called with an API token (see
:data:`app.services.api_tokens.INTERACTIVE_ONLY`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DB, CurrentPrincipal
from app.core.config import get_settings
from app.core.errors import NotFound
from app.models import ApiToken, User
from app.schemas.common import Message
from app.services import api_tokens, audit

router = APIRouter(tags=["api-tokens"])


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    expires_in_days: int = Field(ge=1, le=3650, description="required; at most DBM_API_TOKEN_MAX_DAYS")
    #: omit (null) to inherit all of the owner's permissions; otherwise a non-empty subset
    scopes: list[str] | None = None
    allowed_cidrs: list[str] | None = None


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str] | None
    allowed_cidrs: list[str] | None
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    last_used_ip: str | None
    revoked_at: datetime | None
    created_by: str | None
    status: str


class TokenCreated(TokenOut):
    #: the full token — returned only in this response, never again
    token: str


class TokenPolicy(BaseModel):
    max_days: int
    available_scopes: list[str]


def _out(t: ApiToken) -> TokenOut:
    return TokenOut(
        id=t.id,
        name=t.name,
        prefix=t.prefix,
        scopes=t.scopes,
        allowed_cidrs=t.allowed_cidrs,
        created_at=t.created_at,
        expires_at=t.expires_at,
        last_used_at=t.last_used_at,
        last_used_ip=t.last_used_ip,
        revoked_at=t.revoked_at,
        created_by=t.created_by,
        status=api_tokens.status(t),
    )


async def _tokens(db: DB, user_id: uuid.UUID) -> list[TokenOut]:
    rows = (
        (await db.execute(select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.created_at.desc())))
        .scalars()
        .all()
    )
    return [_out(t) for t in rows]


async def _revoke(db: DB, p: CurrentPrincipal, tok: ApiToken, owner: str) -> Message:
    if tok.revoked_at is None:
        tok.revoked_at = datetime.now(UTC)
        await audit.record(
            "api_token.revoke",
            actor=p,
            category="auth",
            target_type="api_token",
            target_id=str(tok.id),
            object_name=f"{owner}:{tok.name}",
            details={"prefix": tok.prefix},
        )
    return Message(message=f"Token {tok.name} revoked")


@router.get("/me/tokens/policy", response_model=TokenPolicy, summary="Limits and the permissions a new token may carry")
async def token_policy(p: CurrentPrincipal) -> TokenPolicy:
    return TokenPolicy(max_days=get_settings().api_token_max_days, available_scopes=sorted(p.authz.all_permissions()))


@router.get("/me/tokens", response_model=list[TokenOut], summary="Your API tokens")
async def my_tokens(db: DB, p: CurrentPrincipal) -> list[TokenOut]:
    return await _tokens(db, p.user.id)


@router.post("/me/tokens", response_model=TokenCreated, status_code=201, summary="Create an API token (shown once)")
async def create_token(body: TokenCreate, db: DB, p: CurrentPrincipal) -> TokenCreated:
    tok, full = await api_tokens.create(
        db,
        p.user,
        p.authz,
        name=body.name,
        expires_in_days=body.expires_in_days,
        scopes=body.scopes,
        allowed_cidrs=body.allowed_cidrs,
        created_by=p.username,
    )
    await audit.record(
        "api_token.create",
        actor=p,
        category="auth",
        target_type="api_token",
        target_id=str(tok.id),
        object_name=f"{p.username}:{tok.name}",
        details={
            "prefix": tok.prefix,
            "scopes": tok.scopes,
            "expires_at": tok.expires_at.isoformat(),
            "allowed_cidrs": tok.allowed_cidrs,
        },
    )
    return TokenCreated(**_out(tok).model_dump(), token=full)


@router.delete("/me/tokens/{token_id}", response_model=Message, summary="Revoke one of your API tokens")
async def revoke_my_token(token_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    tok = await db.get(ApiToken, token_id)
    if tok is None or tok.user_id != p.user.id:
        raise NotFound("Token not found")
    return await _revoke(db, p, tok, p.username)


@router.get("/users/{user_id}/tokens", response_model=list[TokenOut], summary="A user's API tokens (administrators)")
async def user_tokens(user_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> list[TokenOut]:
    p.authz.require("users.manage")
    if await db.get(User, user_id) is None:
        raise NotFound("User not found")
    return await _tokens(db, user_id)


@router.delete("/users/{user_id}/tokens/{token_id}", response_model=Message, summary="Revoke a user's API token")
async def revoke_user_token(user_id: uuid.UUID, token_id: uuid.UUID, db: DB, p: CurrentPrincipal) -> Message:
    p.authz.require("users.manage")
    user = await db.get(User, user_id)
    tok = await db.get(ApiToken, token_id)
    if user is None or tok is None or tok.user_id != user_id:
        raise NotFound("Token not found")
    return await _revoke(db, p, tok, user.username)
