"""File manifest model for tracking discovered files and detecting changes"""

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from ..core.database import Base


class FileManifest(Base):
    """
    Track discovered files for change detection.

    Used by Active Scanner to detect added/removed/modified files
    without rescanning the entire filesystem.
    """

    __tablename__ = "file_manifests"

    id = Column(Integer, primary_key=True, index=True)

    library_path_id = Column(
        Integer,
        ForeignKey("library_paths.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    file_path = Column(String(1000), unique=True, nullable=False, index=True)
    file_size = Column(BigInteger, nullable=True)
    modified_time = Column(DateTime(timezone=True), nullable=True)

    last_seen = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<FileManifest {self.file_path}>"

    def to_dict(self):
        """Convert to dictionary"""
        return {
            "id": self.id,
            "library_path_id": self.library_path_id,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "modified_time": self.modified_time.isoformat()
            if self.modified_time
            else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
