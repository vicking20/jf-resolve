"""User repository for authentication and user management"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.user import User
from .base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for User operations"""

    def __init__(self, db: AsyncSession):
        super().__init__(User, db)

    async def get_by_username(self, username: str) -> Optional[User]:
        """
        Get user by username

        Args:
            username: Username to search for

        Returns:
            User instance or None
        """
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email

        Args:
            email: Email to search for

        Returns:
            User instance or None
        """
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def username_exists(self, username: str) -> bool:
        """
        Check if username already exists

        Args:
            username: Username to check

        Returns:
            True if exists, False otherwise
        """
        return await self.exists(username=username)

    async def email_exists(self, email: str) -> bool:
        """
        Check if email already exists

        Args:
            email: Email to check

        Returns:
            True if exists, False otherwise
        """
        return await self.exists(email=email)

    async def has_any_users(self) -> bool:
        """
        Check if any users exist in the database

        Returns:
            True if users exist, False otherwise
        """
        count = await self.count()
        return count > 0

    async def get_active_users(self, offset: int = 0, limit: int = 100) -> list[User]:
        """
        Get all active users

        Args:
            offset: Pagination offset
            limit: Pagination limit

        Returns:
            List of active users
        """
        return await self.get_all(offset=offset, limit=limit, is_active=True)

    async def get_admins(self) -> list[User]:
        """
        Get all admin users

        Returns:
            List of admin users
        """
        result = await self.db.execute(select(User).where(User.is_superuser == True))
        return list(result.scalars().all())

    async def deactivate_user(self, user_id: int) -> Optional[User]:
        """
        Deactivate a user (soft delete)

        Args:
            user_id: User ID

        Returns:
            Updated user or None
        """
        return await self.update(user_id, is_active=False)

    async def activate_user(self, user_id: int) -> Optional[User]:
        """
        Activate a user

        Args:
            user_id: User ID

        Returns:
            Updated user or None
        """
        return await self.update(user_id, is_active=True)

    async def make_admin(self, user_id: int) -> Optional[User]:
        """
        Grant admin privileges to a user

        Args:
            user_id: User ID

        Returns:
            Updated user or None
        """
        return await self.update(user_id, is_superuser=True)

    async def revoke_admin(self, user_id: int) -> Optional[User]:
        """
        Revoke admin privileges from a user

        Args:
            user_id: User ID

        Returns:
            Updated user or None
        """
        return await self.update(user_id, is_superuser=False)
