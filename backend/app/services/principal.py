from __future__ import annotations

from dataclasses import dataclass

from app.models import User
from app.services.authz import Authorizer


@dataclass
class Principal:
    """The authenticated caller of a request."""

    user: User
    authz: Authorizer
    auth_source: str
    source_ip: str | None
    request_id: str | None
    session_token_hash: str | None = None
    #: set when the request authenticated with an API token instead of a session
    api_token_id: object | None = None

    @property
    def username(self) -> str:
        return self.user.username
