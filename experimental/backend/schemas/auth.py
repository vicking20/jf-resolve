"""Authentication schemas"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Token(BaseModel):
    """JWT token response"""

    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    """User registration schema"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    is_superuser: bool = False


class UserLogin(BaseModel):
    """User login schema"""

    username: str
    password: str


class UserProfileResponse(BaseModel):
    """User profile response schema"""

    enable_parental_controls: bool = False
    max_content_rating: str = "all"

    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    """User response schema"""

    id: int
    username: str
    is_active: bool
    is_superuser: bool
    permissions: dict | None = None
    created_at: datetime
    profile: Optional[UserProfileResponse] = None

    class Config:
        from_attributes = True


class PasswordChange(BaseModel):
    """Password change schema"""

    current_password: str
    new_password: str = Field(..., min_length=6)
