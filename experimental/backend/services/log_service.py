"""Logging service"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List

from ..core.config import settings


class LogService:
    """Centralized logging service"""

    def __init__(self, log_dir: Path = None, console_output: bool = True):
        self.log_dir = log_dir or settings.LOGS_DIR
        self.log_dir.mkdir(exist_ok=True, parents=True)
        self.console_output = console_output

        # Setup loggers
        self.error_logger = self._setup_logger("error", logging.ERROR)
        self.info_logger = self._setup_logger("info", logging.INFO)
        self.stream_logger = self._setup_logger("stream", logging.DEBUG)

    def _setup_logger(self, name: str, level: int) -> logging.Logger:
        """Setup a logger with rotating file handler and console output"""
        logger = logging.getLogger(f"jfresolve.{name}")
        logger.setLevel(level)

        # Prevent duplicate handlers
        if logger.handlers:
            return logger

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Create rotating file handler (10MB max, 3 backups)
        log_file = self.log_dir / f"{name}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # Add console handler for terminal output
        if self.console_output:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            console_handler.setLevel(level)
            logger.addHandler(console_handler)

        return logger

    def error(self, message: str, **kwargs):
        """Log error message"""
        self.error_logger.error(message, extra=kwargs)

    def info(self, message: str, **kwargs):
        """Log info message"""
        self.info_logger.info(message, extra=kwargs)

    def stream(self, message: str, **kwargs):
        """Log stream resolution"""
        self.stream_logger.info(message, extra=kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message"""
        # Use error logger for warnings since we don't have a separate warning logger
        self.error_logger.warning(message, extra=kwargs)

    def get_logs(
        self, log_type: str = "error", limit: int = 100, offset: int = 0
    ) -> List[str]:
        """Read last N lines from log file with offset"""
        log_file = self.log_dir / f"{log_type}.log"

        if not log_file.exists():
            return []

        try:
            with open(log_file, "r") as f:
                lines = f.readlines()
                lines = [line.rstrip("\n") for line in lines]
                start = -limit - offset if limit + offset <= len(lines) else None
                end = -offset if offset > 0 else None
                return lines[start:end] if start or end else lines
        except Exception as e:
            self.error(f"Failed to read log file {log_type}: {e}")
            return []

    def get_log_file_path(self, log_type: str) -> Path:
        """Get path to log file for download"""
        return self.log_dir / f"{log_type}.log"


# Global log service instance
log_service = LogService()
