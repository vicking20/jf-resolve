"""Authentication schemas"""

from datetime import datetime

from pydantic import BaseModel, Field


class Token(BaseModel):
    """JWT token response"""

    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    """User registration schema"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)


class UserLogin(BaseModel):
    """User login schema"""

    username: str
    password: str


class UserResponse(BaseModel):
    """User response schema"""

    id: int
    username: str
    is_active: bool
    is_superuser: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PasswordChange(BaseModel):
    """Password change schema"""

    current_password: str
    new_password: str = Field(..., min_length=6)
