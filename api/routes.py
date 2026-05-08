"""
API Routes - Router registry
Imports domain routers from focused modules and re-exports them
for backward compatibility with main.py and tests.
"""
import logging

from slowapi import Limiter
from slowapi.util import get_remote_address


logger = logging.getLogger(__name__)

# Rate limiter
limiter = Limiter(key_func=get_remote_address)


# ==========================================
# Import domain routers from focused modules
# ==========================================

# Auth: login, register, password, email verification
from api.auth_routes import auth_router  # noqa: F401  re-exported

# User profile + user management: profile CRUD, avatar, org profile, admin user ops
from api.user_profile_routes import user_profile_router, users_router  # noqa: F401  re-exported

# Re-export request models for backward compatibility

# Alert routes: list, get, acknowledge, resolve, dismiss, digest
from api.alert_routes import alerts_router  # noqa: F401  re-exported

# Newly extracted domain routes
from api.org_routes import org_router  # noqa: F401  re-exported
from api.dashboard_routes import dashboard_router  # noqa: F401  re-exported
from api.usage_routes import usage_router  # noqa: F401  re-exported
from api.watchlist_routes import (
    bulk_import_from_portfolio,  # noqa: F401  re-exported
    bulk_import_watchlist,  # noqa: F401  re-exported
    rescan_all_watchlist,  # noqa: F401  re-exported
    trigger_scan_all,  # noqa: F401  re-exported
    upload_file,  # noqa: F401  re-exported
    upload_with_mapping,  # noqa: F401  re-exported
    watchlist_router,  # noqa: F401  re-exported
    )

# NOTE: Auth routes (register, login, password, email verification) â†’ api/auth_routes.py
# NOTE: User profile routes (profile CRUD, avatar, org profile) â†’ api/user_profile_routes.py
# NOTE: User management routes (list/create/update/deactivate users) â†’ api/user_profile_routes.py


# NOTE: Watchlist upload/template routes -> api/watchlist_routes.py
# NOTE: Watchlist logo routes -> api/watchlist_routes.py


# NOTE: Alert routes (list, get, acknowledge, resolve, dismiss, digest) -> api/alert_routes.py


# NOTE: Dashboard routes -> api/dashboard_routes.py
# NOTE: Admin routes -> api/admin_routes.py
# NOTE: Trademark routes -> api/trademark_routes.py
# NOTE: Usage routes -> api/usage_routes.py
from api.education_routes import education_router  # noqa: F401  re-exported
