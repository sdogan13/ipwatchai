"""
Watchlist Monitoring Worker
Background service that monitors new trademarks against all watchlists
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import time
import signal
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from uuid import UUID
import schedule

from config.settings import settings
from database.crud import Database, get_db_connection, WatchlistCRUD, AlertCRUD
from watchlist.scanner import get_scanner
from notifications.service import NotificationWorker

logger = logging.getLogger(__name__)


class MonitoringWorker:
    """
    Continuous monitoring worker that:
    1. Scans new bulletins/gazettes against all watchlists
    2. Sends notifications for new alerts
    3. Generates scheduled reports
    """
    
    def __init__(self):
        self.running = True
        self.scanner = get_scanner()  # Use singleton for performance
        self.notifier = NotificationWorker()
        
        # Handle shutdown signals
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)
    
    def _shutdown(self, signum, frame):
        """Graceful shutdown"""
        logger.info("🛑 Shutdown signal received...")
        self.running = False
    
    def scan_new_bulletins(self):
        """Scan any unprocessed bulletins against all watchlists"""
        from utils.feature_flags import is_feature_enabled
        if not is_feature_enabled("auto_scan_enabled"):
            logger.info("Auto-scan disabled via feature flag, skipping")
            return

        logger.info("🔍 Checking for new bulletins to scan...")

        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()

                # Find recent bulletins that haven't been scanned against watchlists
                cur.execute("""
                    SELECT DISTINCT bulletin_no as folder_name
                    FROM trademarks
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                    AND bulletin_no IS NOT NULL
                    AND bulletin_no NOT IN (
                        SELECT source_folder FROM scan_jobs
                        WHERE status = 'completed' AND source_folder IS NOT NULL
                    )
                    ORDER BY bulletin_no DESC
                    LIMIT 10
                """)

                new_folders = cur.fetchall()

                if not new_folders:
                    logger.info("   No new bulletins to scan")
                    return

                for folder in new_folders:
                    folder_name = folder['folder_name']
                    logger.info(f"   📂 Scanning {folder_name}...")

                    # Get trademark IDs from this folder
                    cur.execute("""
                        SELECT id FROM trademarks
                        WHERE bulletin_no = %s OR gazette_no = %s
                    """, (folder_name, folder_name))

                    trademark_ids = [UUID(row['id']) for row in cur.fetchall()]

                    if trademark_ids:
                        # Determine source type
                        source_type = 'bulletin' if folder_name.startswith('BLT') else 'gazette'

                        # Run scan
                        alerts = self.scanner.scan_new_trademarks(
                            trademark_ids,
                            source_type,
                            folder_name
                        )

                        logger.info(f"   ✅ {folder_name}: {alerts} alerts generated")
                    else:
                        logger.info(f"   ⚠️ No trademarks found in {folder_name}")

        except Exception as e:
            logger.error(f"❌ Error scanning bulletins: {e}", exc_info=True)
    
    def send_deadline_reminders(self):
        """Send reminders for upcoming opposition deadlines"""
        logger.info("Checking opposition deadline reminders...")

        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()

                # Find alerts with deadlines approaching
                # Remind at: 30 days, 14 days, 7 days, 3 days, 1 day before deadline
                cur.execute("""
                    SELECT a.*,
                           w.brand_name as watched_brand,
                           u.email, u.first_name,
                           (a.opposition_deadline - CURRENT_DATE) as days_remaining
                    FROM alerts_mt a
                    JOIN watchlist_mt w ON a.watchlist_item_id = w.id
                    JOIN users u ON a.user_id = u.id
                    WHERE a.opposition_deadline IS NOT NULL
                    AND a.status NOT IN ('resolved', 'dismissed')
                    AND (a.opposition_deadline - CURRENT_DATE) IN (30, 14, 7, 3, 1)
                    AND u.notify_email = TRUE
                """)

                reminders = cur.fetchall()

                if not reminders:
                    logger.info("   No deadline reminders to send")
                    return

                sent_count = 0
                for reminder in reminders:
                    try:
                        self._send_deadline_reminder_email(
                            email=reminder['email'],
                            user_name=reminder['first_name'] or 'User',
                            brand_name=reminder['watched_brand'],
                            conflicting_name=reminder['conflicting_name'],
                            days_remaining=reminder['days_remaining'],
                            deadline_date=reminder['opposition_deadline']
                        )
                        sent_count += 1
                    except Exception as e:
                        logger.error(f"   Failed to send reminder: {e}")

                logger.info(f"   Sent {sent_count} deadline reminders")

        except Exception as e:
            logger.error(f"Error sending deadline reminders: {e}", exc_info=True)

    def _send_deadline_reminder_email(self, email, user_name, brand_name,
                                      conflicting_name, days_remaining, deadline_date):
        """Send deadline reminder email"""
        from notifications.service import EmailService

        urgency = "URGENT" if days_remaining <= 3 else "Reminder"

        subject = f"{urgency}: {days_remaining} days left to oppose '{conflicting_name}'"

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background: {'#fee2e2' if days_remaining <= 3 else '#fef3c7'};
                        padding: 20px; border-radius: 8px;">
                <h2>Opposition Deadline Reminder</h2>

                <p>Hi {user_name},</p>

                <p><strong>{days_remaining} day(s) remaining</strong> to file opposition against:</p>

                <div style="background: white; padding: 15px; border-radius: 4px; margin: 15px 0;">
                    <p><strong>Your Brand:</strong> {brand_name}</p>
                    <p><strong>Conflicting Mark:</strong> {conflicting_name}</p>
                    <p><strong>Deadline:</strong> {deadline_date}</p>
                </div>

                <p style="color: {'#dc2626' if days_remaining <= 3 else '#d97706'};">
                    {'Act now to protect your trademark!' if days_remaining <= 3
                     else 'Please review and take action if needed.'}
                </p>
            </div>
        </body>
        </html>
        """

        EmailService().send_email(email, subject, html_body)
    
    def process_daily_digest(self):
        """Send daily digest emails"""
        logger.info("📊 Processing daily digest...")
        
        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()
                
                # Find users who need daily digest
                cur.execute("""
                    SELECT DISTINCT u.id, u.email, u.first_name
                    FROM users u
                    WHERE u.notify_email = TRUE
                    AND u.digest_frequency = 'daily'
                    AND u.is_active = TRUE
                    AND EXISTS (
                        SELECT 1 FROM alerts_mt a
                        WHERE a.user_id = u.id
                        AND a.included_in_digest = FALSE
                        AND a.created_at > NOW() - INTERVAL '24 hours'
                    )
                """)
                
                users = cur.fetchall()
                
                for user in users:
                    # Get user's alerts from last 24 hours
                    cur.execute("""
                        SELECT a.*, w.brand_name as watched_brand
                        FROM alerts_mt a
                        JOIN watchlist_mt w ON a.watchlist_item_id = w.id
                        WHERE a.user_id = %s
                        AND a.included_in_digest = FALSE
                        AND a.created_at > NOW() - INTERVAL '24 hours'
                        ORDER BY a.severity DESC, a.created_at DESC
                    """, (str(user['id']),))
                    
                    alerts = [dict(row) for row in cur.fetchall()]
                    
                    if alerts:
                        from notifications.service import EmailService
                        email_service = EmailService()
                        
                        success = email_service.send_daily_digest(
                            to_email=user['email'],
                            user_name=user['first_name'] or 'User',
                            alerts=alerts,
                            period='daily'
                        )
                        
                        if success:
                            # Mark alerts as included in digest
                            alert_ids = [str(a['id']) for a in alerts]
                            cur.execute("""
                                UPDATE alerts_mt
                                SET included_in_digest = TRUE, digest_sent_at = NOW()
                                WHERE id = ANY(%s)
                            """, (alert_ids,))
                            db.commit()
                            logger.info(f"   ✅ Sent digest to {user['email']} ({len(alerts)} alerts)")
                
        except Exception as e:
            logger.error(f"❌ Error processing daily digest: {e}", exc_info=True)
    
    def process_weekly_digest(self):
        """Send weekly digest emails"""
        logger.info("📊 Processing weekly digest...")
        
        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()
                
                # Similar to daily but for weekly users
                cur.execute("""
                    SELECT DISTINCT u.id, u.email, u.first_name
                    FROM users u
                    WHERE u.notify_email = TRUE
                    AND u.digest_frequency = 'weekly'
                    AND u.is_active = TRUE
                    AND EXISTS (
                        SELECT 1 FROM alerts_mt a
                        WHERE a.user_id = u.id
                        AND a.included_in_digest = FALSE
                        AND a.created_at > NOW() - INTERVAL '7 days'
                    )
                """)
                
                users = cur.fetchall()
                
                for user in users:
                    cur.execute("""
                        SELECT a.*, w.brand_name as watched_brand
                        FROM alerts_mt a
                        JOIN watchlist_mt w ON a.watchlist_item_id = w.id
                        WHERE a.user_id = %s
                        AND a.included_in_digest = FALSE
                        AND a.created_at > NOW() - INTERVAL '7 days'
                        ORDER BY a.severity DESC, a.created_at DESC
                    """, (str(user['id']),))
                    
                    alerts = [dict(row) for row in cur.fetchall()]
                    
                    if alerts:
                        from notifications.service import EmailService
                        email_service = EmailService()
                        
                        success = email_service.send_daily_digest(
                            to_email=user['email'],
                            user_name=user['first_name'] or 'User',
                            alerts=alerts,
                            period='weekly'
                        )
                        
                        if success:
                            alert_ids = [str(a['id']) for a in alerts]
                            cur.execute("""
                                UPDATE alerts_mt
                                SET included_in_digest = TRUE, digest_sent_at = NOW()
                                WHERE id = ANY(%s)
                            """, (alert_ids,))
                            db.commit()
                            logger.info(f"   ✅ Sent weekly digest to {user['email']} ({len(alerts)} alerts)")
                
        except Exception as e:
            logger.error(f"❌ Error processing weekly digest: {e}", exc_info=True)
    
    def cleanup_old_data(self):
        """Clean up old sessions, expired reports, etc."""
        logger.info("🧹 Running cleanup...")
        
        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()
                
                # Delete expired sessions
                cur.execute("""
                    DELETE FROM user_sessions WHERE expires_at < NOW()
                """)
                sessions_deleted = cur.rowcount
                
                # Delete expired password reset tokens
                cur.execute("""
                    DELETE FROM password_reset_tokens WHERE expires_at < NOW()
                """)
                
                # Mark old reports as expired
                cur.execute("""
                    UPDATE reports SET status = 'expired'
                    WHERE status = 'completed' 
                    AND expires_at < NOW()
                """)
                
                # Clean up old notifications
                cur.execute("""
                    DELETE FROM notification_queue 
                    WHERE status IN ('sent', 'cancelled', 'failed')
                    AND created_at < NOW() - INTERVAL '30 days'
                """)
                
                db.commit()
                logger.info(f"   ✅ Cleanup complete (deleted {sessions_deleted} expired sessions)")
                
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}", exc_info=True)
    
    def update_alert_deadlines(self):
        """Update days_until_deadline for all pending alerts"""
        logger.info("📅 Updating alert deadlines...")
        
        try:
            with Database(get_db_connection()) as db:
                cur = db.cursor()
                
                cur.execute("""
                    UPDATE alerts_mt
                    SET days_until_deadline = opposition_deadline - CURRENT_DATE
                    WHERE opposition_deadline IS NOT NULL
                    AND status IN ('new', 'seen', 'acknowledged', 'investigating')
                """)
                
                db.commit()
                logger.info(f"   ✅ Updated {cur.rowcount} alert deadlines")
                
        except Exception as e:
            logger.error(f"❌ Error updating deadlines: {e}", exc_info=True)
    
    def run(self):
        """Main worker loop"""
        logger.info("=" * 60)
        logger.info("Trademark Monitoring Worker Started")
        logger.info("=" * 60)

        # Schedule tasks

        # Bulletin scanning - twice daily (data comes every 2 weeks for bulletins, 2 months for gazettes)
        schedule.every().day.at("10:00").do(self.scan_new_bulletins)
        schedule.every().day.at("18:00").do(self.scan_new_bulletins)

        # Deadline reminders - check daily at 08:00 (before digest)
        schedule.every().day.at("08:00").do(self.send_deadline_reminders)

        # Notifications - daily and weekly digests
        schedule.every().day.at("09:00").do(self.process_daily_digest)
        schedule.every().monday.at("09:00").do(self.process_weekly_digest)

        # Maintenance
        schedule.every().day.at("03:00").do(self.cleanup_old_data)
        schedule.every().hour.do(self.update_alert_deadlines)

        # Run initial scan
        self.scan_new_bulletins()

        logger.info("\nScheduled Tasks:")
        logger.info("   - Scan bulletins: daily at 10:00 and 18:00")
        logger.info("   - Deadline reminders: daily at 08:00")
        logger.info("   - Daily digest: 09:00")
        logger.info("   - Weekly digest: Monday 09:00")
        logger.info("   - Cleanup: 03:00 daily")
        logger.info("   - Update deadlines: every hour")
        logger.info("\nPress Ctrl+C to stop\n")
        
        while self.running:
            schedule.run_pending()
            time.sleep(10)
        
        logger.info("👋 Worker stopped")


class SingleScanWorker:
    """
    One-time scan worker for manual triggers or new watchlist items
    """

    def __init__(self):
        self.scanner = get_scanner()  # Use singleton for performance
    
    def scan_watchlist_item(self, watchlist_id: UUID):
        """Scan all trademarks against a single watchlist item"""
        logger.info(f"🔍 Scanning for watchlist item: {watchlist_id}")
        
        alerts = self.scanner.scan_single_watchlist(watchlist_id)
        
        logger.info(f"✅ Scan complete: {alerts} alerts generated")
        return alerts
    
    def scan_folder(self, folder_name: str):
        """Scan a specific folder against all watchlists"""
        logger.info(f"🔍 Scanning folder: {folder_name}")
        
        with Database(get_db_connection()) as db:
            cur = db.cursor()
            
            # Get trademark IDs from folder
            cur.execute("""
                SELECT id FROM trademarks 
                WHERE bulletin_no = %s OR gazette_no = %s
            """, (folder_name, folder_name))
            
            trademark_ids = [UUID(row['id']) for row in cur.fetchall()]
            
            if not trademark_ids:
                logger.warning(f"No trademarks found in {folder_name}")
                return 0
            
            source_type = 'bulletin' if folder_name.startswith('BLT') else 'gazette'
            
            alerts = self.scanner.scan_new_trademarks(
                trademark_ids,
                source_type,
                folder_name
            )
            
            logger.info(f"✅ Scan complete: {alerts} alerts generated")
            return alerts
    
    def full_rescan(self, limit: int = 10000):
        """Rescan all trademarks against all watchlists"""
        logger.info(f"🔍 Starting full rescan (limit: {limit})...")
        
        with Database(get_db_connection()) as db:
            cur = db.cursor()
            
            # Get recent trademark IDs
            cur.execute("""
                SELECT id FROM trademarks 
                WHERE current_status NOT IN ('Refused', 'Withdrawn', 'Expired')
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            
            trademark_ids = [UUID(row['id']) for row in cur.fetchall()]
            
            alerts = self.scanner.scan_new_trademarks(
                trademark_ids,
                'full_rescan',
                f'manual_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}'
            )
            
            logger.info(f"✅ Full rescan complete: {alerts} alerts generated")
            return alerts


# ==========================================
# CLI Entry Point
# ==========================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Watchlist Monitoring Worker")
    parser.add_argument("command", choices=["run", "scan-folder", "scan-watchlist", "full-rescan"],
                        help="Command to execute")
    parser.add_argument("--folder", type=str, help="Folder name to scan (e.g., BLT_500)")
    parser.add_argument("--watchlist-id", type=str, help="Watchlist item ID to scan")
    parser.add_argument("--limit", type=int, default=10000, help="Limit for full rescan")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    if args.command == "run":
        # Run continuous monitoring
        worker = MonitoringWorker()
        worker.run()
        
    elif args.command == "scan-folder":
        if not args.folder:
            print("Error: --folder required")
            sys.exit(1)
        worker = SingleScanWorker()
        worker.scan_folder(args.folder)
        
    elif args.command == "scan-watchlist":
        if not args.watchlist_id:
            print("Error: --watchlist-id required")
            sys.exit(1)
        worker = SingleScanWorker()
        worker.scan_watchlist_item(UUID(args.watchlist_id))
        
    elif args.command == "full-rescan":
        worker = SingleScanWorker()
        worker.full_rescan(args.limit)
