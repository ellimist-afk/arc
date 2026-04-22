"""
JWT authentication module stub - to be implemented
"""

from typing import Optional
from dataclasses import dataclass


@dataclass
class TokenData:
    """Token data"""
    username: Optional[str] = None
    

async def get_current_user(token: str = None):
    """Get current user from token"""
    return TokenData(username="guest")