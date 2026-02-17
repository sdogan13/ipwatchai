"""
Subscription Plan Gating & Usage Credits
=========================================
Checks user's subscription plan and tracks usage for:
- Live search credits
- Lead access credits
- Creative Suite: Unified AI credits (1 credit = name gen, 5 credits = logo gen)
- Trademark application limits

Usage:
    from utils.subscription import check_live_search_eligibility, increment_live_search_usage
    from utils.subscription import check_name_generation_eligibility, check_logo_generation_eligibility
    from utils.subscription import check_ai_credit_eligibility, check_application_eligibility
    from utils.subscription import get_plan_limit, PLAN_FEATURES

    # In endpoint:
    can_search, reason, details = check_live_search_eligibility(db, user_id)
    if not can_search:
        raise HTTPException(status_code=403, detail=details)
"""
import logging
from datetime import datetime, date
from typing import Tuple, Optional

from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ===========================================================================
# Single source of truth for ALL plan limits
# ===========================================================================
PLAN_FEATURES = {
    "free": {
        "price_monthly": 0,
        "price_annual_monthly": 0,
        "monthly_live_searches": 0,
        "daily_lead_views": 0,
        "monthly_reports": 1,
        "can_export_reports": False,
        "name_suggestions_per_session": 3,
        "monthly_ai_credits": 0,
        "monthly_applications": 0,
        "can_track_logos": False,
        "can_view_holder_portfolio": False,
        "can_export_csv_leads": False,
        "can_use_live_scraping": False,
        "max_users": 1,
        "max_watchlist_items": 3,
        "max_daily_quick_searches": 5,
        "auto_scan_max_items": 0,
        "auto_scan_frequency": None,
        "priority_support": False,
        "api_access": False,
        "dedicated_account_manager": False,
    },
    "starter": {
        "price_monthly": 499,
        "price_annual_monthly": 399,
        "monthly_live_searches": 10,
        "daily_lead_views": 0,
        "monthly_reports": 10,
        "can_export_reports": True,
        "name_suggestions_per_session": 10,
        "monthly_ai_credits": 30,
        "monthly_applications": 1,
        "can_track_logos": True,
        "can_view_holder_portfolio": False,
        "can_export_csv_leads": False,
        "can_use_live_scraping": True,
        "max_users": 3,
        "max_watchlist_items": 15,
        "max_daily_quick_searches": 50,
        "auto_scan_max_items": 15,
        "auto_scan_frequency": "weekly",
        "priority_support": False,
        "api_access": False,
        "dedicated_account_manager": False,
    },
    "business": {
        "price_monthly": 799,
        "price_annual_monthly": 639,
        "monthly_live_searches": 30,
        "daily_lead_views": 5,
        "monthly_reports": 20,
        "can_export_reports": True,
        "name_suggestions_per_session": 20,
        "monthly_ai_credits": 100,
        "monthly_applications": 3,
        "can_track_logos": True,
        "can_view_holder_portfolio": True,
        "can_export_csv_leads": False,
        "can_use_live_scraping": True,
        "max_users": 5,
        "max_watchlist_items": 50,
        "max_daily_quick_searches": 150,
        "auto_scan_max_items": 50,
        "auto_scan_frequency": "weekly",
        "priority_support": False,
        "api_access": False,
        "dedicated_account_manager": False,
    },
    "professional": {
        "price_monthly": 1299,
        "price_annual_monthly": 1039,
        "monthly_live_searches": 100,
        "daily_lead_views": 10,
        "monthly_reports": 30,
        "can_export_reports": True,
        "name_suggestions_per_session": 30,
        "monthly_ai_credits": 300,
        "monthly_applications": 5,
        "can_track_logos": True,
        "can_view_holder_portfolio": True,
        "can_export_csv_leads": False,
        "can_use_live_scraping": True,
        "max_users": 10,
        "max_watchlist_items": 1000,
        "max_daily_quick_searches": 2000,
        "auto_scan_max_items": 100,
        "auto_scan_frequency": "daily",
        "priority_support": True,
        "api_access": False,
        "dedicated_account_manager": False,
    },
    "enterprise": {
        "price_monthly": 2999,
        "price_annual_monthly": 2399,
        "monthly_live_searches": 999999,
        "daily_lead_views": 999999,
        "monthly_reports": 999999,
        "can_export_reports": True,
        "name_suggestions_per_session": 999999,
        "monthly_ai_credits": 999999,
        "monthly_applications": 999999,
        "can_track_logos": True,
        "can_view_holder_portfolio": True,
        "can_export_csv_leads": True,
        "can_use_live_scraping": True,
        "max_users": 999999,
        "max_watchlist_items": 999999,
        "max_daily_quick_searches": 999999,
        "auto_scan_max_items": 999999,
        "auto_scan_frequency": "daily",
        "priority_support": True,
        "api_access": True,
        "dedicated_account_manager": True,
    },
    "superadmin": {
        "price_monthly": 0,
        "price_annual_monthly": 0,
        "monthly_live_searches": 999999,
        "daily_lead_views": 999999,
        "monthly_reports": 999999,
        "can_export_reports": True,
        "name_suggestions_per_session": 999999,
        "monthly_ai_credits": 999999,
        "monthly_applications": 999999,
        "can_track_logos": True,
        "can_view_holder_portfolio": True,
        "can_export_csv_leads": True,
        "can_use_live_scraping": True,
        "max_users": 999999,
        "max_watchlist_items": 999999,
        "max_daily_quick_searches": 999999,
        "auto_scan_max_items": 999999,
        "auto_scan_frequency": "daily",
        "priority_support": True,
        "api_access": True,
        "dedicated_account_manager": True,
    },
}


def get_plan_limit(plan_name: str, feature: str):
    """
    Get a plan limit. Checks DB override first (via settings_manager),
    then falls back to code default in PLAN_FEATURES.
    DB key format: plan.{plan_name}.{feature}
    """
    from utils.settings_manager import settings_manager
    db_key = f"plan.{plan_name}.{feature}"
    db_value = settings_manager.get(db_key)
    if db_value is not None:
        return db_value

    # Fall back to code default
    plan = PLAN_FEATURES.get(plan_name, PLAN_FEATURES["free"])
    return plan.get(feature, PLAN_FEATURES["free"].get(feature, 0))


def get_user_plan(db, user_id: str) -> dict:
    """
    Get user's effective subscription plan.
    Checks individual_plan_id first, then falls back to organization plan.
    Super admins always get enterprise-level access.
    If subscription_end_date has passed, treats the plan as expired (free).

    Args:
        db: Database context manager instance
        user_id: UUID string of the user

    Returns:
        dict with keys: plan_name, can_use_live_search, monthly_limit, display_name
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            COALESCE(sp_user.name, sp_org.name, 'free') as plan_name,
            COALESCE(sp_user.display_name, sp_org.display_name, 'Free Trial') as display_name,
            COALESCE(sp_user.can_use_live_search, sp_org.can_use_live_search, FALSE) as can_use_live_search,
            COALESCE(u.is_superadmin, FALSE) as is_superadmin,
            o.subscription_end_date
        FROM users u
        LEFT JOIN subscription_plans sp_user ON u.individual_plan_id = sp_user.id
        LEFT JOIN organizations o ON u.organization_id = o.id
        LEFT JOIN subscription_plans sp_org ON o.subscription_plan_id = sp_org.id
        WHERE u.id = %s
    """, (user_id,))

    row = cur.fetchone()
    if not row:
        return {
            'plan_name': 'free',
            'display_name': 'Free Trial',
            'can_use_live_search': False,
            'monthly_limit': 0,
        }

    plan_name = row['plan_name']
    display_name = row['display_name']

    # Super admins get unlimited access
    if row['is_superadmin']:
        plan_name = 'superadmin'
        display_name = 'Super Admin'
    elif plan_name != 'free' and row.get('subscription_end_date'):
        # Check if subscription has expired
        end_date = row['subscription_end_date']
        if hasattr(end_date, 'date'):
            end_date = end_date.date()
        if end_date < date.today():
            logger.info(f"Subscription expired for user={user_id}, was={plan_name}, end={end_date}")
            plan_name = 'free'
            display_name = 'Free Trial'

    return {
        'plan_name': plan_name,
        'display_name': display_name,
        'can_use_live_search': row['can_use_live_search'] or row['is_superadmin'],
        'monthly_limit': get_plan_limit(plan_name, 'monthly_live_searches'),
    }


def get_live_search_usage(db, user_id: str) -> int:
    """
    Get current month's live search usage count.
    Sums api_usage.live_searches for all rows in the current month.

    Args:
        db: Database context manager instance
        user_id: UUID string

    Returns:
        Total live searches this month
    """
    cur = db.cursor(cursor_factory=RealDictCursor)

    # First day of current month
    today = date.today()
    month_start = today.replace(day=1)

    cur.execute("""
        SELECT COALESCE(SUM(live_searches), 0) as total
        FROM api_usage
        WHERE user_id = %s AND usage_date >= %s
    """, (user_id, month_start))

    row = cur.fetchone()
    return row['total'] if row else 0


def increment_live_search_usage(db, user_id: str, org_id: str = None) -> int:
    """
    Increment live search counter for today.
    Uses upsert (INSERT ... ON CONFLICT DO UPDATE) on (user_id, usage_date).

    Args:
        db: Database context manager instance
        user_id: UUID string
        org_id: Organization UUID string (optional)

    Returns:
        Today's new live_searches count
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    today = date.today()

    cur.execute("""
        INSERT INTO api_usage (user_id, organization_id, usage_date, live_searches)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET
            live_searches = api_usage.live_searches + 1,
            updated_at = CURRENT_TIMESTAMP
        RETURNING live_searches
    """, (user_id, org_id, today))

    db.commit()
    row = cur.fetchone()
    return row['live_searches'] if row else 1


def check_live_search_eligibility(db, user_id: str) -> Tuple[bool, str, dict]:
    """
    Check if user can perform a live search.

    Args:
        db: Database context manager instance
        user_id: UUID string

    Returns:
        (can_search, reason, details)

    Reasons:
        - "ok": User can search
        - "upgrade_required": Plan doesn't include live search
        - "limit_exceeded": Monthly limit reached
    """
    plan = get_user_plan(db, user_id)
    plan_name = plan['plan_name']
    can_use = plan['can_use_live_search']
    monthly_limit = plan['monthly_limit']

    if not can_use:
        logger.info(f"Feature denied: user={user_id} plan={plan_name} feature=live_search reason=upgrade_required")
        return False, "upgrade_required", {
            "error": "upgrade_required",
            "current_plan": plan_name,
            "display_name": plan['display_name'],
            "required_plan": "professional",
            "message": "Canli arama Premium ozelligidir. Professional veya Enterprise plana yukseltmeniz gerekiyor.",
            "message_en": "Live search is a Premium feature. Upgrade to Professional or Enterprise.",
        }

    current_usage = get_live_search_usage(db, user_id)

    if current_usage >= monthly_limit:
        logger.info(f"Plan limit reached: user={user_id} plan={plan_name} feature=live_search limit={monthly_limit}")
        return False, "limit_exceeded", {
            "error": "limit_exceeded",
            "current_plan": plan_name,
            "display_name": plan['display_name'],
            "monthly_limit": monthly_limit,
            "current_usage": current_usage,
            "remaining": 0,
            "message": f"Bu ay {monthly_limit} canli arama hakkinin tamamini kullandiniz.",
            "message_en": f"You've used all {monthly_limit} live searches this month.",
        }

    remaining = monthly_limit - current_usage
    return True, "ok", {
        "current_plan": plan_name,
        "display_name": plan['display_name'],
        "monthly_limit": monthly_limit,
        "current_usage": current_usage,
        "remaining": remaining,
    }


def get_daily_quick_searches(db, user_id: str) -> int:
    """Get today's quick search count for a user."""
    cur = db.cursor(cursor_factory=RealDictCursor)
    today = date.today()

    cur.execute("""
        SELECT COALESCE(quick_searches, 0) as total
        FROM api_usage
        WHERE user_id = %s AND usage_date = %s
    """, (user_id, today))

    row = cur.fetchone()
    return row['total'] if row else 0


def increment_quick_search_usage(db, user_id: str, org_id: str = None) -> int:
    """Increment quick search counter for today. Returns new count."""
    cur = db.cursor(cursor_factory=RealDictCursor)
    today = date.today()

    cur.execute("""
        INSERT INTO api_usage (user_id, organization_id, usage_date, quick_searches)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET
            quick_searches = api_usage.quick_searches + 1,
            updated_at = CURRENT_TIMESTAMP
        RETURNING quick_searches
    """, (user_id, org_id, today))

    db.commit()
    row = cur.fetchone()
    return row['quick_searches'] if row else 1


def check_quick_search_eligibility(db, user_id: str) -> Tuple[bool, str, dict]:
    """
    Check if user can perform a quick search today.

    Returns:
        (can_search, reason, details)
    """
    plan = get_user_plan(db, user_id)
    plan_name = plan['plan_name']
    daily_limit = get_plan_limit(plan_name, 'max_daily_quick_searches')
    used_today = get_daily_quick_searches(db, user_id)

    if used_today >= daily_limit:
        logger.info(f"Plan limit reached: user={user_id} plan={plan_name} feature=quick_search limit={daily_limit}")
        return False, "daily_limit_exceeded", {
            "error": "daily_limit_exceeded",
            "current_plan": plan_name,
            "daily_limit": daily_limit,
            "used_today": used_today,
            "remaining": 0,
            "message": f"Gunluk {daily_limit} arama limitinize ulastiniz. Yarin tekrar deneyebilirsiniz.",
            "message_en": f"You've reached your daily limit of {daily_limit} searches. Try again tomorrow.",
        }

    remaining = daily_limit - used_today

    # Abuse indicator: 80% of daily cap consumed
    if daily_limit > 0 and used_today >= daily_limit * 0.8:
        logger.info(f"High usage: user={user_id} plan={plan_name} feature=quick_search used={used_today}/{daily_limit}")

    return True, "ok", {
        "current_plan": plan_name,
        "daily_limit": daily_limit,
        "used_today": used_today,
        "remaining": remaining,
    }


def get_lead_access(db, user_id: str) -> dict:
    """
    Get user's lead access permissions and remaining daily credits.

    Args:
        db: Database context manager instance
        user_id: UUID string

    Returns:
        dict with: plan_name, can_access, daily_limit, used_today, remaining
    """
    plan = get_user_plan(db, user_id)
    plan_name = plan['plan_name']
    daily_limit = get_plan_limit(plan_name, 'daily_lead_views')

    if daily_limit == 0:
        return {
            'plan_name': plan_name,
            'can_access': False,
            'daily_limit': 0,
            'used_today': 0,
            'remaining': 0,
        }

    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT COUNT(*) as cnt
        FROM lead_access_log
        WHERE user_id = %s
          AND action = 'viewed'
          AND created_at::date = CURRENT_DATE
    """, (user_id,))
    used_today = cur.fetchone()['cnt']

    if daily_limit == -1:
        remaining = -1
    else:
        remaining = max(0, daily_limit - used_today)

    return {
        'plan_name': plan_name,
        'can_access': True,
        'daily_limit': daily_limit,
        'used_today': used_today,
        'remaining': remaining,
    }


# ============================================================
# Creative Suite: Organization Plan + AI Credits
# ============================================================

def get_org_plan(db, org_id: str) -> dict:
    """
    Get an organization's subscription plan.

    Args:
        db: Database context manager instance
        org_id: Organization UUID string

    Returns:
        dict with keys: plan_name, display_name
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            COALESCE(sp.name, 'free') as plan_name,
            COALESCE(sp.display_name, 'Free Trial') as display_name
        FROM organizations o
        LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
        WHERE o.id = %s
    """, (org_id,))

    row = cur.fetchone()
    if not row:
        return {
            'plan_name': 'free',
            'display_name': 'Free Trial',
        }

    return dict(row)


# ============================================================
# Unified AI Credits (1 credit = name gen, 5 credits = logo gen)
# ============================================================

def _reset_monthly_ai_credits_if_needed(db, org_id: str) -> None:
    """
    Reset ai_credits_monthly to plan limit if the reset date is from a previous month.
    Called internally before checking AI credit eligibility.
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            o.ai_credits_reset_at,
            COALESCE(sp.name, 'free') as plan_name
        FROM organizations o
        LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
        WHERE o.id = %s
    """, (org_id,))
    row = cur.fetchone()
    if not row:
        return

    reset_at = row['ai_credits_reset_at']
    plan_name = row['plan_name']
    plan_limit = get_plan_limit(plan_name, 'monthly_ai_credits')
    now = datetime.utcnow()

    if reset_at is None or (reset_at.year, reset_at.month) < (now.year, now.month):
        cur.execute("""
            UPDATE organizations
            SET ai_credits_monthly = %s,
                ai_credits_reset_at = %s
            WHERE id = %s
        """, (plan_limit, now, org_id))
        db.commit()
        logger.info(f"Reset monthly AI credits for org {org_id}: {plan_limit}")


def check_ai_credit_eligibility(db, org_id: str, cost: int) -> Tuple[bool, str, dict]:
    """
    Check if an organization has enough AI credits for an operation.

    Args:
        db: Database context manager instance
        org_id: Organization UUID string
        cost: Number of credits required (1 for name gen, 5 for logo gen)

    Returns:
        (can_use, reason, details)
    """
    _reset_monthly_ai_credits_if_needed(db, org_id)

    plan = get_org_plan(db, org_id)
    plan_name = plan['plan_name']
    monthly_limit = get_plan_limit(plan_name, 'monthly_ai_credits')

    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            COALESCE(ai_credits_monthly, 0) as ai_credits_monthly,
            COALESCE(ai_credits_purchased, 0) as ai_credits_purchased
        FROM organizations WHERE id = %s
    """, (org_id,))
    row = cur.fetchone()

    if not row:
        return False, "upgrade_required", {
            "error": "upgrade_required",
            "current_plan": plan_name,
            "message": "Organizasyon bulunamadi.",
            "message_en": "Organization not found.",
        }

    monthly = row['ai_credits_monthly']
    purchased = row['ai_credits_purchased']
    total_remaining = monthly + purchased

    if total_remaining >= cost:
        return True, "ok", {
            "current_plan": plan_name,
            "display_name": plan['display_name'],
            "monthly_remaining": monthly,
            "purchased_remaining": purchased,
            "total_remaining": total_remaining,
            "monthly_limit": monthly_limit,
        }

    return False, "credits_exhausted", {
        "error": "credits_exhausted",
        "current_plan": plan_name,
        "display_name": plan['display_name'],
        "monthly_remaining": monthly,
        "purchased_remaining": purchased,
        "total_remaining": total_remaining,
        "monthly_limit": monthly_limit,
        "cost": cost,
        "message": f"AI kredi bakiyeniz yetersiz ({total_remaining} mevcut, {cost} gerekli).",
        "message_en": f"Insufficient AI credits ({total_remaining} available, {cost} required).",
    }


def deduct_ai_credits(db, org_id: str, cost: int) -> bool:
    """
    Deduct AI credits from the organization.
    Uses monthly credits first, then falls back to purchased credits.

    Args:
        db: Database context manager instance
        org_id: Organization UUID string
        cost: Number of credits to deduct

    Returns:
        True if credits were deducted, False if insufficient credits
    """
    cur = db.cursor(cursor_factory=RealDictCursor)

    # Try monthly credits first
    cur.execute("""
        UPDATE organizations
        SET ai_credits_monthly = ai_credits_monthly - %s
        WHERE id = %s AND ai_credits_monthly >= %s
        RETURNING ai_credits_monthly
    """, (cost, org_id, cost))
    db.commit()
    row = cur.fetchone()
    if row is not None:
        return True

    # Check if we can split across monthly + purchased
    cur.execute("""
        SELECT
            COALESCE(ai_credits_monthly, 0) as monthly,
            COALESCE(ai_credits_purchased, 0) as purchased
        FROM organizations WHERE id = %s
    """, (org_id,))
    row = cur.fetchone()
    if not row:
        return False

    monthly = row['monthly']
    purchased = row['purchased']

    if monthly + purchased >= cost:
        remainder = cost - monthly
        cur.execute("""
            UPDATE organizations
            SET ai_credits_monthly = 0,
                ai_credits_purchased = ai_credits_purchased - %s
            WHERE id = %s AND ai_credits_purchased >= %s
            RETURNING ai_credits_purchased
        """, (remainder, org_id, remainder))
        db.commit()
        row = cur.fetchone()
        return row is not None

    return False


def refund_ai_credits(db, org_id: str, cost: int) -> bool:
    """
    Refund AI credits to the organization's monthly pool.

    Args:
        db: Database context manager instance
        org_id: Organization UUID string
        cost: Number of credits to refund

    Returns:
        True if credits were refunded
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        UPDATE organizations
        SET ai_credits_monthly = ai_credits_monthly + %s
        WHERE id = %s
        RETURNING ai_credits_monthly
    """, (cost, org_id))
    db.commit()
    row = cur.fetchone()
    if row is not None:
        logger.info(f"Refunded {cost} AI credits for org {org_id}, monthly now: {row['ai_credits_monthly']}")
        return True
    return False


# ============================================================
# Creative Suite: Name Generation (uses AI credits, cost=1)
# ============================================================

def get_monthly_name_generations(db, org_id: str) -> int:
    """
    Get current month's name generation count for an organization.
    Sums api_usage.name_generations for all users in the org this month.
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    today = date.today()
    month_start = today.replace(day=1)

    cur.execute("""
        SELECT COALESCE(SUM(name_generations), 0) as total
        FROM api_usage
        WHERE organization_id = %s AND usage_date >= %s
    """, (org_id, month_start))

    row = cur.fetchone()
    return row['total'] if row else 0


def increment_name_generation_usage(db, user_id: str, org_id: str) -> int:
    """
    Increment name generation counter for today.
    Uses upsert on (user_id, usage_date).

    Returns:
        Today's new name_generations count for this user.
    """
    cur = db.cursor(cursor_factory=RealDictCursor)
    today = date.today()

    cur.execute("""
        INSERT INTO api_usage (user_id, organization_id, usage_date, name_generations)
        VALUES (%s, %s, %s, 1)
        ON CONFLICT (user_id, usage_date)
        DO UPDATE SET
            name_generations = api_usage.name_generations + 1,
            updated_at = CURRENT_TIMESTAMP
        RETURNING name_generations
    """, (user_id, org_id, today))

    db.commit()
    row = cur.fetchone()
    return row['name_generations'] if row else 1


def check_name_generation_eligibility(db, org_id: str, session_count: int) -> Tuple[bool, str, dict]:
    """
    Check if an organization can generate more name suggestions.
    Enforces per-session cap first, then checks unified AI credits (cost=1).

    Args:
        db: Database context manager instance
        org_id: Organization UUID string
        session_count: Number of names already generated in this session

    Returns:
        (can_generate, reason, details)
    """
    plan = get_org_plan(db, org_id)
    plan_name = plan['plan_name']

    # --- Per-session soft cap (UX) ---
    session_limit = get_plan_limit(plan_name, 'name_suggestions_per_session')

    # Unlimited session (enterprise/superadmin)
    if session_limit < 999999 and session_count >= session_limit:
        # Over session limit — check purchased credits
        cur = db.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT COALESCE(name_credits_purchased, 0) as name_credits_purchased
            FROM organizations WHERE id = %s
        """, (org_id,))
        row = cur.fetchone()
        purchased = row['name_credits_purchased'] if row else 0

        if purchased <= 0:
            return False, "upgrade_required", {
                "error": "upgrade_required",
                "current_plan": plan_name,
                "display_name": plan['display_name'],
                "session_limit": session_limit,
                "session_count": session_count,
                "remaining": 0,
                "message": f"Bu oturumda {session_limit} isim onerisi hakkini kullandiniz. Daha fazlasi icin planunuzi yukseltebilirsiniz.",
                "message_en": f"You've used all {session_limit} name suggestions for this session. Upgrade for more.",
            }

    # --- Check unified AI credits (cost=1 per name generation) ---
    can_use, reason, details = check_ai_credit_eligibility(db, org_id, cost=1)
    if not can_use:
        return False, "monthly_limit_exceeded", {
            "error": "credits_exhausted",
            "current_plan": plan_name,
            "display_name": plan['display_name'],
            "remaining": 0,
            "message": "AI kredi bakiyeniz yetersiz.",
            "message_en": "Insufficient AI credits.",
        }

    return True, "ok", {
        "current_plan": plan_name,
        "display_name": plan['display_name'],
        "session_limit": session_limit,
        "session_count": session_count,
        "monthly_limit": details.get('monthly_limit', 0),
        "remaining": details.get('total_remaining', 0),
    }


def deduct_name_credit(db, org_id: str) -> bool:
    """
    Deduct one AI credit for name generation (cost=1).
    Falls back to purchased name credits if AI credits insufficient.

    Returns:
        True if a credit was deducted
    """
    if deduct_ai_credits(db, org_id, cost=1):
        return True

    # Fall back to legacy purchased name credits
    cur = db.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        UPDATE organizations
        SET name_credits_purchased = name_credits_purchased - 1
        WHERE id = %s AND name_credits_purchased > 0
        RETURNING name_credits_purchased
    """, (org_id,))
    db.commit()
    row = cur.fetchone()
    return row is not None


# ============================================================
# Creative Suite: Logo Generation (uses AI credits, cost=5)
# ============================================================

def _reset_monthly_logo_credits_if_needed(db, org_id: str) -> None:
    """
    Legacy: Reset logo_credits_monthly. Kept for backward compatibility
    but new flow uses unified AI credits via _reset_monthly_ai_credits_if_needed.
    """
    _reset_monthly_ai_credits_if_needed(db, org_id)


def check_logo_generation_eligibility(db, org_id: str) -> Tuple[bool, str, dict]:
    """
    Check if an organization can run a logo generation (cost=5 AI credits).

    Returns:
        (can_generate, reason, details)
    """
    return check_ai_credit_eligibility(db, org_id, cost=5)


def deduct_logo_credit(db, org_id: str) -> bool:
    """
    Deduct 5 AI credits for logo generation.

    Returns:
        True if credits were deducted
    """
    return deduct_ai_credits(db, org_id, cost=5)


def refund_logo_credit(db, org_id: str) -> bool:
    """
    Refund 5 AI credits for a failed logo generation.

    Returns:
        True if credits were refunded
    """
    return refund_ai_credits(db, org_id, cost=5)


# ============================================================
# Reports
# ============================================================

def check_report_eligibility(db, user_plan: str, org_id: str) -> dict:
    """
    Check if an organization can generate more reports this month.

    Args:
        db: Database context manager instance
        user_plan: Plan name string (free, starter, professional, enterprise)
        org_id: Organization UUID string

    Returns:
        dict with: eligible, reports_used, reports_limit, can_export, reason
    """
    reports_limit = get_plan_limit(user_plan, 'monthly_reports')
    can_export = get_plan_limit(user_plan, 'can_export_reports')

    cur = db.cursor(cursor_factory=RealDictCursor)

    # Count reports created this calendar month for this org
    today = date.today()
    month_start = today.replace(day=1)

    cur.execute("""
        SELECT COUNT(*) as cnt
        FROM reports
        WHERE organization_id = %s
          AND created_at >= %s
    """, (org_id, month_start))
    row = cur.fetchone()
    reports_used = row['cnt'] if row else 0

    if reports_used >= reports_limit:
        return {
            'eligible': False,
            'reports_used': reports_used,
            'reports_limit': reports_limit,
            'can_export': can_export,
            'reason': f"Bu ay {reports_limit} rapor hakkinin tamamini kullandiniz.",
        }

    return {
        'eligible': True,
        'reports_used': reports_used,
        'reports_limit': reports_limit,
        'can_export': can_export,
        'reason': None,
    }


# ============================================================
# Trademark Applications
# ============================================================

def get_monthly_applications(db, org_id: str) -> int:
    """Get current month's application count for an organization."""
    cur = db.cursor(cursor_factory=RealDictCursor)
    today = date.today()
    month_start = today.replace(day=1)

    cur.execute("""
        SELECT COUNT(*) as cnt
        FROM trademark_applications_mt
        WHERE organization_id = %s
          AND created_at >= %s
    """, (org_id, month_start))

    row = cur.fetchone()
    return row['cnt'] if row else 0


def check_application_eligibility(db, user_id: str, org_id: str) -> Tuple[bool, str, dict]:
    """
    Check if an organization can create more trademark applications this month.

    Args:
        db: Database context manager instance
        user_id: UUID string of the user
        org_id: Organization UUID string

    Returns:
        (can_create, reason, details)
    """
    plan = get_user_plan(db, user_id)
    plan_name = plan['plan_name']
    monthly_limit = get_plan_limit(plan_name, 'monthly_applications')

    if monthly_limit == 0:
        return False, "upgrade_required", {
            "error": "upgrade_required",
            "current_plan": plan_name,
            "display_name": plan['display_name'],
            "monthly_limit": 0,
            "monthly_used": 0,
            "message": "Marka basvurusu olusturmak icin planunuzi yukseltmeniz gerekiyor.",
            "message_en": "Upgrade your plan to create trademark applications.",
        }

    monthly_used = get_monthly_applications(db, org_id)

    if monthly_used >= monthly_limit:
        return False, "limit_exceeded", {
            "error": "limit_exceeded",
            "current_plan": plan_name,
            "display_name": plan['display_name'],
            "monthly_limit": monthly_limit,
            "monthly_used": monthly_used,
            "remaining": 0,
            "message": f"Bu ay {monthly_limit} basvuru hakkinin tamamini kullandiniz.",
            "message_en": f"You've used all {monthly_limit} application credits this month.",
        }

    remaining = monthly_limit - monthly_used
    return True, "ok", {
        "current_plan": plan_name,
        "display_name": plan['display_name'],
        "monthly_limit": monthly_limit,
        "monthly_used": monthly_used,
        "remaining": remaining,
    }
