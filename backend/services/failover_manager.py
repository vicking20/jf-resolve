"""Failover state management"""

from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.failover_state import FailoverState as FailoverStateModel


class FailoverManager:
    """Manage failover state for stream resolution"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_state(self, state_key: str) -> FailoverStateModel:
        """Get or create failover state"""
        result = await self.db.execute(
            select(FailoverStateModel).where(FailoverStateModel.state_key == state_key)
        )
        state = result.scalar_one_or_none()

        if not state:
            state = FailoverStateModel(
                state_key=state_key, current_index=0, attempt_count=0
            )
            self.db.add(state)
            await self.db.commit()
            await self.db.refresh(state)

        return state

    async def update_state(self, state: FailoverStateModel):
        """Update failover state"""
        await self.db.commit()
        await self.db.refresh(state)

    def should_failover(
        self,
        state: FailoverStateModel,
        grace_seconds: int = 45,
        reset_seconds: int = 120,
    ) -> Tuple[bool, int]:
        """
        Determine if should failover and which index to use

        Returns:
            (should_increment, index_to_use)

        Logic:
        1. If last_attempt > reset_seconds ago → RESET to index 0
        2. If first_attempt < grace_seconds ago → GRACE PERIOD, keep current index
        3. Otherwise → FAILOVER, increment index
        """
        now = datetime.utcnow()

        # RESET: Too much time passed, assume success
        if (
            state.last_attempt
            and (now - state.last_attempt).total_seconds() > reset_seconds
        ):
            return False, 0

        # GRACE PERIOD: Keep serving same link (allows buffering)
        if (
            state.first_attempt
            and (now - state.first_attempt).total_seconds() < grace_seconds
        ):
            return False, state.current_index

        # FAILOVER: Try next index
        return True, state.current_index + 1

    async def cleanup_old_states(self, days: int = 7):
        """Clean up failover states older than X days"""
        cutoff = datetime.utcnow() - timedelta(days=days)

        result = await self.db.execute(
            select(FailoverStateModel).where(FailoverStateModel.updated_at < cutoff)
        )
        old_states = result.scalars().all()

        for state in old_states:
            await self.db.delete(state)

        await self.db.commit()
        return len(old_states)
