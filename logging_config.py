"""
Structured Logging Configuration

Provides JSON logging for production and colored console logging for development.
Includes request ID tracking and timing decorators for performance monitoring.

Usage:
    from logging_config import get_logger, log_timing, setup_logging

    # Setup logging (call once at startup)
    setup_logging()

    # Get a logger for your module
    logger = get_logger(__name__)

    # Log with extra context
    logger.info("Query completed", extra={"duration_ms": 145, "candidates": 30})

    # Use timing decorator
    @log_timing("process_batch")
    def process_batch(data):
        ...
"""

import os
import sys
import json
import time
import logging
import functools
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Callable
from contextvars import ContextVar
from pathlib import Path

# =============================================================================
# Configuration
# =============================================================================

# Environment-based configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "auto")  # "json", "console", or "auto"
LOG_FILE = os.getenv("LOG_FILE", "")  # Optional file path
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Auto-detect format based on environment
if LOG_FORMAT == "auto":
    LOG_FORMAT = "json" if ENVIRONMENT == "production" else "console"

# Request ID context variable (thread-safe)
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


# =============================================================================
# Request ID Management
# =============================================================================

def set_request_id(request_id: str) -> None:
    """Set the current request ID for logging context."""
    request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """Get the current request ID."""
    return request_id_var.get()


def clear_request_id() -> None:
    """Clear the current request ID."""
    request_id_var.set(None)


# =============================================================================
# JSON Formatter (Production)
# =============================================================================

class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for production environments.

    Outputs structured JSON logs suitable for log aggregation systems
    like ELK, Datadog, or CloudWatch.
    """

    def __init__(self, **kwargs):
        super().__init__()
        self.default_fields = kwargs

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }

        # Add request ID if available
        request_id = get_request_id()
        if request_id:
            log_entry["request_id"] = request_id

        # Add thread info for debugging
        log_entry["thread"] = threading.current_thread().name

        # Add default fields
        log_entry.update(self.default_fields)

        # Add extra fields from the log record
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in (
                    "name", "msg", "args", "created", "filename", "funcName",
                    "levelname", "levelno", "lineno", "module", "msecs",
                    "pathname", "process", "processName", "relativeCreated",
                    "stack_info", "exc_info", "exc_text", "thread", "threadName",
                    "message", "taskName"
                ):
                    # Serialize non-standard types
                    try:
                        json.dumps(value)
                        log_entry[key] = value
                    except (TypeError, ValueError):
                        log_entry[key] = str(value)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info)
            }

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# =============================================================================
# Colored Console Formatter (Development)
# =============================================================================

class ColoredFormatter(logging.Formatter):
    """
    Colored console formatter for development environments.

    Provides readable, color-coded log output with context information.
    """

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    def __init__(self, use_colors: bool = True):
        super().__init__()
        self.use_colors = use_colors and sys.stdout.isatty()

    def format(self, record: logging.LogRecord) -> str:
        # Timestamp
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # Level with color
        level = record.levelname
        if self.use_colors:
            color = self.COLORS.get(level, "")
            level_str = f"{color}{level:8}{self.RESET}"
        else:
            level_str = f"{level:8}"

        # Module/function info
        location = f"{record.module}.{record.funcName}"
        if self.use_colors:
            location = f"{self.DIM}{location}{self.RESET}"

        # Message
        message = record.getMessage()

        # Base log line
        log_line = f"{timestamp} {level_str} [{location}] {message}"

        # Add extra fields
        extra_fields = {}
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "message", "taskName"
            ):
                extra_fields[key] = value

        if extra_fields:
            if self.use_colors:
                extras_str = f" {self.DIM}| {extra_fields}{self.RESET}"
            else:
                extras_str = f" | {extra_fields}"
            log_line += extras_str

        # Add request ID if present
        request_id = get_request_id()
        if request_id:
            if self.use_colors:
                log_line = f"{self.DIM}[{request_id[:8]}]{self.RESET} {log_line}"
            else:
                log_line = f"[{request_id[:8]}] {log_line}"

        # Add exception info
        if record.exc_info:
            log_line += "\n" + "".join(traceback.format_exception(*record.exc_info))

        return log_line


# =============================================================================
# Custom Logger Class
# =============================================================================

class StructuredLogger(logging.Logger):
    """
    Extended logger with convenience methods for structured logging.
    """

    def _log_with_context(
        self,
        level: int,
        msg: str,
        *args,
        exc_info=None,
        stack_info=False,
        stacklevel=1,
        **kwargs
    ):
        """Log with additional context fields."""
        extra = kwargs.pop("extra", {})
        extra.update(kwargs)
        super()._log(
            level, msg, args,
            exc_info=exc_info,
            stack_info=stack_info,
            stacklevel=stacklevel + 1,
            extra=extra
        )

    def debug(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.DEBUG, msg, *args, stacklevel=2, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.INFO, msg, *args, stacklevel=2, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.WARNING, msg, *args, stacklevel=2, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.ERROR, msg, *args, stacklevel=2, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self._log_with_context(logging.CRITICAL, msg, *args, stacklevel=2, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        kwargs["exc_info"] = True
        self._log_with_context(logging.ERROR, msg, *args, stacklevel=2, **kwargs)


# Register custom logger class
logging.setLoggerClass(StructuredLogger)


# =============================================================================
# Setup Functions
# =============================================================================

def setup_logging(
    level: str = LOG_LEVEL,
    format_type: str = LOG_FORMAT,
    log_file: Optional[str] = LOG_FILE or None,
    app_name: str = "trademark-system"
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: "json" for production, "console" for development
        log_file: Optional file path for file logging
        app_name: Application name for JSON logs
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    if format_type == "json":
        console_handler.setFormatter(JSONFormatter(app=app_name, env=ENVIRONMENT))
    else:
        console_handler.setFormatter(ColoredFormatter())

    root_logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        file_handler.setFormatter(JSONFormatter(app=app_name, env=ENVIRONMENT))
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> StructuredLogger:
    """
    Get a structured logger for the given module name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        StructuredLogger instance
    """
    return logging.getLogger(name)


# =============================================================================
# Timing Decorators
# =============================================================================

def log_timing(
    operation: str = None,
    level: int = logging.INFO,
    log_args: bool = False,
    threshold_ms: float = 0
) -> Callable:
    """
    Decorator to log function execution time.

    Args:
        operation: Operation name (defaults to function name)
        level: Log level for timing messages
        log_args: Whether to log function arguments
        threshold_ms: Only log if execution time exceeds this threshold

    Example:
        @log_timing("process_batch")
        def process_batch(data):
            ...

        @log_timing(threshold_ms=100)  # Only log slow operations
        def maybe_slow_operation():
            ...
    """
    def decorator(func: Callable) -> Callable:
        op_name = operation or func.__name__
        logger = get_logger(func.__module__)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            error = None
            result = None

            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                error = e
                raise
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000

                if duration_ms >= threshold_ms:
                    log_data = {
                        "operation": op_name,
                        "duration_ms": round(duration_ms, 2),
                        "status": "error" if error else "success"
                    }

                    if log_args:
                        log_data["args_count"] = len(args)
                        log_data["kwargs_keys"] = list(kwargs.keys())

                    if error:
                        log_data["error"] = str(error)
                        logger.error(f"{op_name} failed", **log_data)
                    else:
                        logger.info(f"{op_name} completed", **log_data)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            error = None

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                error = e
                raise
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000

                if duration_ms >= threshold_ms:
                    log_data = {
                        "operation": op_name,
                        "duration_ms": round(duration_ms, 2),
                        "status": "error" if error else "success"
                    }

                    if log_args:
                        log_data["args_count"] = len(args)
                        log_data["kwargs_keys"] = list(kwargs.keys())

                    if error:
                        log_data["error"] = str(error)
                        logger.error(f"{op_name} failed", **log_data)
                    else:
                        logger.info(f"{op_name} completed", **log_data)

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def log_batch_stats(
    operation: str,
    total: int,
    processed: int,
    skipped: int = 0,
    errors: int = 0,
    duration_ms: float = None,
    **extra
) -> None:
    """
    Log batch processing statistics.

    Args:
        operation: Name of the batch operation
        total: Total items in batch
        processed: Successfully processed items
        skipped: Skipped items
        errors: Failed items
        duration_ms: Total duration in milliseconds
        **extra: Additional fields to log
    """
    logger = get_logger("batch")

    stats = {
        "operation": operation,
        "total": total,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "success_rate": round(processed / max(total, 1) * 100, 1),
        **extra
    }

    if duration_ms is not None:
        stats["duration_ms"] = round(duration_ms, 2)
        stats["items_per_sec"] = round(total / max(duration_ms / 1000, 0.001), 1)

    logger.info(f"Batch {operation} completed", **stats)


# =============================================================================
# Context Managers
# =============================================================================

class LogContext:
    """
    Context manager for adding temporary context to logs.

    Example:
        with LogContext(request_id="abc123", user_id=42):
            logger.info("Processing request")  # Includes request_id and user_id
    """

    _local = threading.local()

    def __init__(self, **context):
        self.context = context
        self.previous_context = {}

    def __enter__(self):
        if not hasattr(self._local, "context"):
            self._local.context = {}

        # Save previous values and update
        for key, value in self.context.items():
            self.previous_context[key] = self._local.context.get(key)
            self._local.context[key] = value

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore previous values
        for key, value in self.previous_context.items():
            if value is None:
                self._local.context.pop(key, None)
            else:
                self._local.context[key] = value

        return False

    @classmethod
    def get_context(cls) -> Dict[str, Any]:
        """Get current logging context."""
        if not hasattr(cls._local, "context"):
            return {}
        return cls._local.context.copy()


# =============================================================================
# FastAPI Middleware Integration
# =============================================================================

import uuid


class RequestLoggingMiddleware:
    """
    FastAPI/Starlette middleware for request logging.

    Adds request ID tracking and logs request/response details.

    Usage:
        from logging_config import RequestLoggingMiddleware
        app.add_middleware(RequestLoggingMiddleware)
    """

    def __init__(self, app):
        self.app = app
        self.logger = get_logger("api.requests")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Generate request ID
        request_id = str(uuid.uuid4())
        set_request_id(request_id)

        # Extract request info
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")
        query_string = scope.get("query_string", b"").decode()

        # Log request start
        start_time = time.perf_counter()
        self.logger.info(
            "Request started",
            request_id=request_id,
            method=method,
            path=path,
            query=query_string or None
        )

        # Track response status
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            self.logger.exception(
                "Request failed with exception",
                request_id=request_id,
                method=method,
                path=path,
                error=str(e)
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000

            log_level = logging.INFO if status_code < 400 else logging.WARNING
            if status_code >= 500:
                log_level = logging.ERROR

            self.logger.log(
                log_level,
                "Request completed",
                request_id=request_id,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=round(duration_ms, 2)
            )

            clear_request_id()


# =============================================================================
# Import asyncio for decorator type checking
# =============================================================================
import asyncio


# =============================================================================
# Auto-setup on import (optional, can be disabled)
# =============================================================================

# Uncomment to auto-setup logging when module is imported:
# setup_logging()
