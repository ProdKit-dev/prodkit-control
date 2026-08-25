"""Controlled PostgreSQL executor for allowlisted, parameterized operations."""

from .executor import ConstrainedDatabaseExecutor, DatabaseExecutorConfig

__all__ = ["ConstrainedDatabaseExecutor", "DatabaseExecutorConfig"]
__version__ = "0.9.0"
