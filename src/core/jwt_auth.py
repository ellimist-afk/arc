"""
JWT authentication module stub - to be implemented

There is no login yet. The dashboard listens on loopback and
AuthenticationMiddleware refuses cross-origin writes, so whoever reaches an
endpoint is the local operator, and the stub user carries admin rights.
Endpoints check roles/permissions/twitch_id; without these fields every one
of them raised AttributeError and answered 500.
"""

from typing import List, Optional
from dataclasses import dataclass, field


@dataclass
class TokenData:
    """Token data"""
    username: Optional[str] = None
    twitch_id: Optional[str] = None
    roles: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)


async def get_current_user(token: str = None):
    """Get current user from token"""
    return TokenData(username="local", roles=["admin"], permissions=["bot:manage"])
