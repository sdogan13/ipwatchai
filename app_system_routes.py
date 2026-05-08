"""Public/system endpoint registration helpers for the legacy FastAPI app."""

from datetime import datetime


def register_system_routes(app, settings):
    """Register public config, info, health, and status endpoints."""

    @app.get("/api/v1/config")
    async def get_app_config():
        """Return application configuration for frontend alignment."""
        from risk_engine import RISK_THRESHOLDS

        return {
            "risk_thresholds": {key: int(value * 100) for key, value in RISK_THRESHOLDS.items()},
        }

    @app.get("/api/info", tags=["Root"])
    async def api_info():
        """API info endpoint - returns basic info."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "status": "running",
            "docs": "/docs" if settings.debug else "disabled",
            "health": "/health",
        }

    @app.get("/health", tags=["Health"])
    async def health_check():
        """Health check endpoint."""
        health_status = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "version": settings.app_version,
            "checks": {},
        }

        try:
            from database.crud import get_db_connection

            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.close()
            conn.close()
            health_status["checks"]["database"] = "ok"
        except Exception as exc:
            health_status["checks"]["database"] = f"error: {str(exc)}"
            health_status["status"] = "degraded"

        try:
            import redis

            redis_client = redis.Redis(
                host=settings.redis.host,
                port=settings.redis.port,
                password=settings.redis.password,
            )
            redis_client.ping()
            health_status["checks"]["redis"] = "ok"
        except Exception as exc:
            health_status["checks"]["redis"] = f"error: {str(exc)}"
            health_status["status"] = "degraded"

        try:
            import torch

            if torch.cuda.is_available():
                health_status["checks"]["gpu"] = f"ok ({torch.cuda.get_device_name(0)})"
            else:
                health_status["checks"]["gpu"] = "cpu only"
        except Exception:
            health_status["checks"]["gpu"] = "not available"

        return health_status

    @app.get("/api/v1/status", tags=["Status"])
    async def api_status():
        """API status with database statistics."""
        from database.crud import Database, get_db_connection

        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()
                cur.execute("SELECT COUNT(*) FROM trademarks")
                trademark_count = cur.fetchone()["count"]

                cur.execute(
                    "SELECT MAX(bulletin_date) as latest FROM trademarks WHERE bulletin_date IS NOT NULL"
                )
                row = cur.fetchone()
                last_bulletin = row["latest"].isoformat() if row and row["latest"] else None

                # Tasarım (design) live count — parallel to total_trademarks so
                # the dashboard search panel can show a per-registry count.
                design_count = 0
                try:
                    cur.execute(
                        "SELECT COUNT(*) FROM designs WHERE registry_type = 'design'"
                    )
                    design_count = cur.fetchone()["count"]
                except Exception:
                    design_count = 0

                return {
                    "status": "operational",
                    "statistics": {
                        "total_trademarks": trademark_count,
                        "total_designs": design_count,
                        "last_bulletin_date": last_bulletin,
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                }
        except Exception:
            return {
                "status": "error",
                "statistics": {
                    "total_trademarks": 0,
                    "total_designs": 0,
                    "last_bulletin_date": None,
                },
                "timestamp": datetime.utcnow().isoformat(),
            }
