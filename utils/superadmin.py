"""
Superadmin seeding utility.
Promotes the SUPERADMIN_EMAIL user to superadmin on startup. Idempotent.
"""
import logging

from config.settings import settings
from database.crud import get_db_connection

logger = logging.getLogger(__name__)


def seed_superadmin():
    """Promote SUPERADMIN_EMAIL user to superadmin on startup. Idempotent."""
    superadmin_email = settings.superadmin_email
    if not superadmin_email:
        logger.info("SUPERADMIN_EMAIL not set, skipping superadmin seed")
        return

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET is_superadmin = TRUE "
            "WHERE email = %s AND is_superadmin = FALSE "
            "RETURNING id",
            (superadmin_email,),
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()

        if updated:
            logger.info(f"Superadmin granted to {superadmin_email}")
        else:
            logger.info(f"Superadmin seed: no action needed for {superadmin_email}")
    except Exception as e:
        logger.warning(f"Superadmin seed failed (non-fatal): {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
