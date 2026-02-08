"""
Database Connection Pool Module

Provides thread-safe connection pooling for PostgreSQL using psycopg2.
Connections are automatically returned to the pool when released.

Usage:
    # Context manager (recommended)
    with connection_context() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")

    # Manual management
    conn = get_connection()
    try:
        # use connection
    finally:
        release_connection(conn)

    # Cleanup on shutdown
    close_pool()
"""

import os
import logging
import threading
import atexit
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup logging
logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

# Try to import from centralized settings, fall back to environment variables
try:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "trademark-system"))
    from config.settings import settings

    DB_CONFIG = {
        "dbname": settings.database.name,
        "user": settings.database.user,
        "password": settings.database.password,
        "host": settings.database.host,
        "port": settings.database.port,
    }
    MIN_CONNECTIONS = settings.database.pool_min_size
    MAX_CONNECTIONS = settings.database.pool_max_size

    logger.info("Database config loaded from settings")
except ImportError:
    # Fallback to environment variables
    DB_CONFIG = {
        "dbname": os.getenv("DB_NAME", "trademark_db"),
        "user": os.getenv("DB_USER", "turk_patent"),
        "password": os.getenv("DB_PASSWORD"),
        "host": os.getenv("DB_HOST", "host.docker.internal"),
        "port": int(os.getenv("DB_PORT", 5432)),
    }
    MIN_CONNECTIONS = int(os.getenv("DB_POOL_MIN", 5))
    MAX_CONNECTIONS = int(os.getenv("DB_POOL_MAX", 20))

    logger.info("Database config loaded from environment")


# =============================================================================
# Connection Pool Singleton
# =============================================================================

class DatabasePool:
    """
    Thread-safe singleton connection pool manager.

    Uses psycopg2.pool.ThreadedConnectionPool for multi-threaded applications.
    """

    _instance: Optional['DatabasePool'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                # Double-check locking pattern
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._pool: Optional[pg_pool.ThreadedConnectionPool] = None
        self._pool_lock = threading.Lock()
        self._initialized = True
        self._closed = False

        # Statistics
        self._stats = {
            "connections_created": 0,
            "connections_returned": 0,
            "connections_failed": 0,
            "current_used": 0,
        }
        self._stats_lock = threading.Lock()

    def initialize(
        self,
        minconn: int = MIN_CONNECTIONS,
        maxconn: int = MAX_CONNECTIONS,
        **kwargs
    ) -> None:
        """
        Initialize the connection pool.

        Args:
            minconn: Minimum number of connections to maintain
            maxconn: Maximum number of connections allowed
            **kwargs: Additional connection parameters
        """
        with self._pool_lock:
            if self._pool is not None:
                logger.warning("Pool already initialized, skipping")
                return

            config = {**DB_CONFIG, **kwargs}

            try:
                self._pool = pg_pool.ThreadedConnectionPool(
                    minconn=minconn,
                    maxconn=maxconn,
                    **config
                )
                self._closed = False
                logger.info(
                    f"Connection pool initialized: "
                    f"min={minconn}, max={maxconn}, "
                    f"host={config['host']}:{config['port']}/{config['dbname']}"
                )
            except psycopg2.Error as e:
                logger.error(f"Failed to initialize connection pool: {e}")
                raise

    def get_connection(self):
        """
        Get a connection from the pool.

        Returns:
            psycopg2 connection object

        Raises:
            RuntimeError: If pool is not initialized or closed
            psycopg2.pool.PoolError: If pool is exhausted
        """
        # Check if we need to initialize (without holding _pool_lock to avoid deadlock)
        if self._pool is None:
            self.initialize()

        with self._pool_lock:
            if self._closed:
                raise RuntimeError("Connection pool is closed")

        try:
            conn = self._pool.getconn()

            # Ensure connection is valid
            if conn.closed:
                logger.warning("Got closed connection from pool, reconnecting...")
                self._pool.putconn(conn, close=True)
                conn = self._pool.getconn()

            # Test connection
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
            except psycopg2.Error:
                logger.warning("Connection test failed, reconnecting...")
                self._pool.putconn(conn, close=True)
                conn = self._pool.getconn()

            with self._stats_lock:
                self._stats["connections_created"] += 1
                self._stats["current_used"] += 1

            return conn

        except pg_pool.PoolError as e:
            with self._stats_lock:
                self._stats["connections_failed"] += 1
            logger.error(f"Failed to get connection from pool: {e}")
            raise

    def release_connection(self, conn, close: bool = False) -> None:
        """
        Return a connection to the pool.

        Args:
            conn: Connection to return
            close: If True, close the connection instead of returning it
        """
        if self._pool is None:
            logger.warning("Pool not initialized, closing connection directly")
            if conn and not conn.closed:
                conn.close()
            return

        try:
            # Rollback any uncommitted transaction
            if not conn.closed:
                try:
                    conn.rollback()
                except psycopg2.Error:
                    pass

            self._pool.putconn(conn, close=close)

            with self._stats_lock:
                self._stats["connections_returned"] += 1
                self._stats["current_used"] = max(0, self._stats["current_used"] - 1)

        except Exception as e:
            logger.error(f"Error returning connection to pool: {e}")
            # Try to close the connection directly
            if conn and not conn.closed:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Close all connections in the pool."""
        with self._pool_lock:
            if self._pool is not None and not self._closed:
                try:
                    self._pool.closeall()
                    logger.info("Connection pool closed")
                except Exception as e:
                    logger.error(f"Error closing pool: {e}")
                finally:
                    self._closed = True
                    self._pool = None

    @property
    def stats(self) -> dict:
        """Get pool statistics."""
        with self._stats_lock:
            return self._stats.copy()

    @property
    def is_initialized(self) -> bool:
        """Check if pool is initialized."""
        return self._pool is not None and not self._closed

    def health_check(self) -> dict:
        """
        Perform a health check on the pool.

        Returns:
            dict with status and latency information
        """
        import time

        if not self.is_initialized:
            return {"status": "not_initialized"}

        try:
            start = time.perf_counter()
            conn = self.get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
                cur.close()
                latency_ms = round((time.perf_counter() - start) * 1000, 2)

                return {
                    "status": "healthy",
                    "latency_ms": latency_ms,
                    "stats": self.stats
                }
            finally:
                self.release_connection(conn)

        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e)
            }


# =============================================================================
# Module-Level Convenience Functions
# =============================================================================

# Singleton instance
_pool_instance: Optional[DatabasePool] = None


def get_pool() -> DatabasePool:
    """Get the singleton pool instance."""
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = DatabasePool()
    return _pool_instance


def get_connection():
    """
    Get a connection from the pool.

    Returns:
        psycopg2 connection object
    """
    return get_pool().get_connection()


def release_connection(conn, close: bool = False) -> None:
    """
    Return a connection to the pool.

    Args:
        conn: Connection to return
        close: If True, close the connection instead of returning it
    """
    get_pool().release_connection(conn, close=close)


def close_pool() -> None:
    """Close the connection pool."""
    pool = get_pool()
    pool.close()


@contextmanager
def connection_context(cursor_factory=None):
    """
    Context manager for database connections.

    Automatically returns connection to pool when done.
    Rolls back on exception, commits on success if autocommit is False.

    Args:
        cursor_factory: Optional cursor factory (e.g., RealDictCursor)

    Yields:
        psycopg2 connection object

    Example:
        with connection_context() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users")
            users = cur.fetchall()
            conn.commit()
    """
    conn = get_connection()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        release_connection(conn)


@contextmanager
def cursor_context(cursor_factory=None, commit: bool = True):
    """
    Context manager for database cursors.

    Automatically handles connection and cursor lifecycle.

    Args:
        cursor_factory: Optional cursor factory (e.g., RealDictCursor)
        commit: If True, commit transaction on success

    Yields:
        psycopg2 cursor object

    Example:
        with cursor_context() as cur:
            cur.execute("SELECT * FROM users")
            users = cur.fetchall()
    """
    conn = get_connection()
    try:
        if cursor_factory:
            cur = conn.cursor(cursor_factory=cursor_factory)
        else:
            cur = conn.cursor()

        try:
            yield cur
            if commit:
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    finally:
        release_connection(conn)


# =============================================================================
# Cleanup on Exit
# =============================================================================

def _cleanup_on_exit():
    """Cleanup function registered with atexit."""
    global _pool_instance
    if _pool_instance is not None:
        try:
            _pool_instance.close()
        except Exception as e:
            logger.error(f"Error during pool cleanup: {e}")


# Register cleanup function
atexit.register(_cleanup_on_exit)


# =============================================================================
# Legacy Compatibility
# =============================================================================

def get_db_connection():
    """
    Legacy function for backwards compatibility.

    DEPRECATED: Use get_connection() or connection_context() instead.
    """
    logger.warning(
        "get_db_connection() is deprecated. "
        "Use get_connection() or connection_context() instead."
    )
    return get_connection()
