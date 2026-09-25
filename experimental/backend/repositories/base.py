"""Base repository with common CRUD operations"""

from typing import Any, Dict, Generic, List, Optional, Type, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import Base
from ..core.logging import get_logger

logger = get_logger(__name__)

# Type variable for model classes
ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Base repository with common database operations

    Provides:
    - CRUD operations (Create, Read, Update, Delete)
    - Pagination
    - Filtering
    - Bulk operations
    """

    def __init__(self, model: Type[ModelType], db: AsyncSession):
        """
        Initialize repository

        Args:
            model: SQLAlchemy model class
            db: Database session
        """
        self.model = model
        self.db = db

    async def get(self, id: int) -> Optional[ModelType]:
        """
        Get a single record by ID

        Args:
            id: Record ID

        Returns:
            Model instance or None
        """
        result = await self.db.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def get_by(self, **filters) -> Optional[ModelType]:
        """
        Get a single record by filters

        Args:
            **filters: Column=value filters

        Returns:
            Model instance or None
        """
        query = select(self.model)

        for key, value in filters.items():
            if hasattr(self.model, key):
                query = query.where(getattr(self.model, key) == value)

        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def get_all(
        self,
        offset: int = 0,
        limit: int = 100,
        **filters,
    ) -> List[ModelType]:
        """
        Get all records with optional pagination and filtering

        Args:
            offset: Number of records to skip
            limit: Maximum number of records to return
            **filters: Column=value filters

        Returns:
            List of model instances
        """
        query = select(self.model)

        # Apply filters
        for key, value in filters.items():
            if hasattr(self.model, key):
                query = query.where(getattr(self.model, key) == value)

        # Apply pagination
        query = query.offset(offset).limit(limit)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def count(self, **filters) -> int:
        """
        Count records with optional filtering

        Args:
            **filters: Column=value filters

        Returns:
            Number of matching records
        """
        query = select(func.count()).select_from(self.model)

        # Apply filters
        for key, value in filters.items():
            if hasattr(self.model, key):
                query = query.where(getattr(self.model, key) == value)

        result = await self.db.execute(query)
        return result.scalar_one()

    async def create(self, **data) -> ModelType:
        """
        Create a new record

        Args:
            **data: Column=value data for new record

        Returns:
            Created model instance
        """
        instance = self.model(**data)
        self.db.add(instance)
        await self.db.commit()
        await self.db.refresh(instance)

        logger.debug(f"Created {self.model.__name__} with ID {instance.id}")
        return instance

    async def update(self, id: int, **data) -> Optional[ModelType]:
        """
        Update a record

        Args:
            id: Record ID
            **data: Column=value data to update

        Returns:
            Updated model instance or None if not found
        """
        instance = await self.get(id)

        if instance is None:
            return None

        for key, value in data.items():
            if hasattr(instance, key):
                setattr(instance, key, value)

        await self.db.commit()
        await self.db.refresh(instance)

        logger.debug(f"Updated {self.model.__name__} with ID {id}")
        return instance

    async def delete(self, id: int) -> bool:
        """
        Delete a record

        Args:
            id: Record ID

        Returns:
            True if deleted, False if not found
        """
        instance = await self.get(id)

        if instance is None:
            return False

        await self.db.delete(instance)
        await self.db.commit()

        logger.debug(f"Deleted {self.model.__name__} with ID {id}")
        return True

    async def delete_by(self, **filters) -> int:
        """
        Delete records matching filters

        Args:
            **filters: Column=value filters

        Returns:
            Number of deleted records
        """
        query = delete(self.model)

        for key, value in filters.items():
            if hasattr(self.model, key):
                query = query.where(getattr(self.model, key) == value)

        result = await self.db.execute(query)
        await self.db.commit()

        deleted_count = result.rowcount
        logger.debug(f"Deleted {deleted_count} {self.model.__name__} records")
        return deleted_count

    async def exists(self, **filters) -> bool:
        """
        Check if a record exists

        Args:
            **filters: Column=value filters

        Returns:
            True if exists, False otherwise
        """
        count = await self.count(**filters)
        return count > 0

    async def bulk_create(self, items: List[Dict[str, Any]]) -> List[ModelType]:
        """
        Create multiple records

        Args:
            items: List of dictionaries with record data

        Returns:
            List of created model instances
        """
        instances = [self.model(**data) for data in items]
        self.db.add_all(instances)
        await self.db.commit()

        for instance in instances:
            await self.db.refresh(instance)

        logger.debug(f"Bulk created {len(instances)} {self.model.__name__} records")
        return instances

    async def bulk_update(self, updates: List[Dict[str, Any]]) -> int:
        """
        Update multiple records

        Args:
            updates: List of dicts with 'id' and update data

        Returns:
            Number of updated records
        """
        count = 0

        for update_data in updates:
            id = update_data.pop("id", None)
            if id:
                instance = await self.update(id, **update_data)
                if instance:
                    count += 1

        logger.debug(f"Bulk updated {count} {self.model.__name__} records")
        return count

    async def get_or_create(
        self,
        defaults: Optional[Dict[str, Any]] = None,
        **filters,
    ) -> tuple[ModelType, bool]:
        """
        Get a record or create it if it doesn't exist

        Args:
            defaults: Default values for creation
            **filters: Filters to find existing record

        Returns:
            Tuple of (instance, created) where created is True if new
        """
        instance = await self.get_by(**filters)

        if instance is not None:
            return instance, False

        # Create new record
        create_data = {**filters}
        if defaults:
            create_data.update(defaults)

        instance = await self.create(**create_data)
        return instance, True

    async def refresh(self, instance: ModelType) -> ModelType:
        """
        Refresh an instance from the database

        Args:
            instance: Model instance to refresh

        Returns:
            Refreshed instance
        """
        await self.db.refresh(instance)
        return instance
