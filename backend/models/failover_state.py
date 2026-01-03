"""Failover state model"""

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from ..database import Base


class FailoverState(Base):
    """Failover state tracking for stream resolution"""

    __tablename__ = "failover_state"

    id = Column(Integer, primary_key=True, index=True)
    state_key = Column(
        String(255), unique=True, nullable=False, index=True
    )  # "movie:550" or "tv:1234:1:5"
    current_index = Column(Integer, default=0)
    first_attempt = Column(DateTime(timezone=True))
    last_attempt = Column(DateTime(timezone=True))
    attempt_count = Column(Integer, default=0)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self):
        return f"<FailoverState {self.state_key} index={self.current_index}>"
