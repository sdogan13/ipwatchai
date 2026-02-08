"""
Database CRUD Operations
All database interactions go through this module
"""
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from uuid import UUID, uuid4

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from config.settings import settings
from auth.authentication import hash_password
from models.schemas import (
    OrganizationCreate, OrganizationUpdate,
    UserCreate, UserUpdate, UserRole,
    WatchlistItemCreate, WatchlistItemUpdate,
    AlertStatus, AlertSeverity,
    PlanType
)

logger = logging.getLogger(__name__)


# ==========================================
# Database Connection
# ==========================================

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        dbname=settings.database.name,
        user=settings.database.user,
        password=settings.database.password,
        host=settings.database.host,
        port=settings.database.port
    )


class Database:
    """Database operations class"""
    
    def __init__(self, conn=None):
        self.conn = conn or get_db_connection()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.conn.rollback()
        self.conn.close()
    
    def cursor(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)
    
    def commit(self):
        self.conn.commit()
    
    def rollback(self):
        self.conn.rollback()


# ==========================================
# Organization CRUD
# ==========================================

class OrganizationCRUD:
    
    @staticmethod
    def create(db: Database, data: OrganizationCreate) -> Dict:
        """Create new organization"""
        cur = db.cursor()

        # Generate unique slug by appending number if duplicate exists
        base_slug = data.slug
        slug = base_slug
        counter = 1
        while True:
            cur.execute("SELECT id FROM organizations WHERE slug = %s", (slug,))
            if not cur.fetchone():
                break
            slug = f"{base_slug}-{counter}"
            counter += 1

        org_id = uuid4()
        cur.execute("""
            INSERT INTO organizations (id, name, slug, email, phone, address)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (str(org_id), data.name, slug, data.email, data.phone, data.address))
        
        db.commit()
        return dict(cur.fetchone())
    
    @staticmethod
    def get_by_id(db: Database, org_id: UUID) -> Optional[Dict]:
        """Get organization by ID"""
        cur = db.cursor()
        cur.execute("SELECT * FROM organizations WHERE id = %s", (str(org_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def get_by_slug(db: Database, slug: str) -> Optional[Dict]:
        """Get organization by slug"""
        cur = db.cursor()
        cur.execute("SELECT * FROM organizations WHERE slug = %s", (slug,))
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def update(db: Database, org_id: UUID, data) -> Optional[Dict]:
        """Update organization - accepts OrganizationUpdate object or dict"""
        cur = db.cursor()

        # Handle both dict and object input
        def get_val(key):
            if isinstance(data, dict):
                return data.get(key)
            return getattr(data, key, None)

        updates = []
        values = []

        # All supported fields
        field_mappings = [
            ('name', 'name'),
            ('email', 'email'),
            ('phone', 'phone'),
            ('address', 'address'),
            ('tax_id', 'tax_id'),
            ('industry', 'industry'),
            ('website', 'website'),
            ('email_notifications', 'email_notifications'),
            ('weekly_report', 'weekly_report'),
            ('risk_threshold', 'default_alert_threshold'),
        ]

        for field_name, column_name in field_mappings:
            val = get_val(field_name)
            if val is not None:
                updates.append(f"{column_name} = %s")
                values.append(val)

        # Handle settings separately (needs JSON conversion)
        settings_val = get_val('settings')
        if settings_val is not None:
            updates.append("settings = %s")
            values.append(psycopg2.extras.Json(settings_val))

        if not updates:
            return OrganizationCRUD.get_by_id(db, org_id)

        values.append(str(org_id))
        cur.execute(f"""
            UPDATE organizations SET {', '.join(updates)}
            WHERE id = %s RETURNING *
        """, values)

        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def get_stats(db: Database, org_id: UUID) -> Dict:
        """Get organization statistics"""
        cur = db.cursor()
        cur.execute("""
            SELECT * FROM org_dashboard_stats WHERE organization_id = %s
        """, (str(org_id),))
        row = cur.fetchone()
        return dict(row) if row else {}
    
    @staticmethod
    def check_limits(db: Database, org_id: UUID, resource: str) -> Tuple[bool, int, int]:
        """
        Check if organization is within limits based on subscription plan.
        Returns: (within_limit, current_count, max_allowed)
        """
        from utils.subscription import get_plan_limit

        cur = db.cursor()
        cur.execute("""
            SELECT COALESCE(sp.name, 'free') as plan_name
            FROM organizations o
            LEFT JOIN subscription_plans sp ON o.subscription_plan_id = sp.id
            WHERE o.id = %s
        """, (str(org_id),))
        row = cur.fetchone()

        if not row:
            return False, 0, 0

        plan_name = row['plan_name']

        if resource == "users":
            cur.execute("SELECT COUNT(*) FROM users WHERE organization_id = %s AND is_active = TRUE", (str(org_id),))
            current = cur.fetchone()['count']
            max_users = get_plan_limit(plan_name, 'max_users')
            return current < max_users, current, max_users

        elif resource == "watchlist":
            cur.execute("SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s AND is_active = TRUE", (str(org_id),))
            current = cur.fetchone()['count']
            max_items = get_plan_limit(plan_name, 'max_watchlist_items')
            return current < max_items, current, max_items

        return True, 0, 0


# ==========================================
# User CRUD
# ==========================================

class UserCRUD:
    
    @staticmethod
    def create(db: Database, org_id: UUID, data: UserCreate) -> Dict:
        """Create new user"""
        cur = db.cursor()
        
        # Check email uniqueness
        cur.execute("SELECT id FROM users WHERE email = %s", (data.email,))
        if cur.fetchone():
            raise ValueError(f"Email '{data.email}' already registered")
        
        # Check organization limits
        within_limit, _, _ = OrganizationCRUD.check_limits(db, org_id, "users")
        if not within_limit:
            raise ValueError("Organization has reached maximum user limit")
        
        user_id = uuid4()
        password_hash = hash_password(data.password)
        
        cur.execute("""
            INSERT INTO users (id, organization_id, email, password_hash, first_name, last_name, phone, role)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, organization_id, email, first_name, last_name, phone, role, 
                      is_active, is_email_verified, created_at
        """, (str(user_id), str(org_id), data.email, password_hash, 
              data.first_name, data.last_name, data.phone, data.role.value))
        
        db.commit()
        return dict(cur.fetchone())
    
    @staticmethod
    def get_by_id(db: Database, user_id: UUID) -> Optional[Dict]:
        """Get user by ID"""
        cur = db.cursor()
        cur.execute("""
            SELECT id, organization_id, email, first_name, last_name, phone, role,
                   is_active, is_email_verified, COALESCE(is_superadmin, FALSE) as is_superadmin,
                   last_login_at, created_at,
                   avatar_url, title, department, linkedin
            FROM users WHERE id = %s
        """, (str(user_id),))
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def get_by_email(db: Database, email: str) -> Optional[Dict]:
        """Get user by email (includes password hash for auth)"""
        cur = db.cursor()
        cur.execute("""
            SELECT id, organization_id, email, password_hash, first_name, last_name, 
                   phone, role, is_active, is_email_verified
            FROM users WHERE email = %s
        """, (email,))
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def get_by_organization(db: Database, org_id: UUID, include_inactive: bool = False) -> List[Dict]:
        """Get all users in organization"""
        cur = db.cursor()
        query = """
            SELECT id, organization_id, email, first_name, last_name, phone, role,
                   is_active, is_email_verified, last_login_at, created_at
            FROM users WHERE organization_id = %s
        """
        if not include_inactive:
            query += " AND is_active = TRUE"
        query += " ORDER BY created_at"
        
        cur.execute(query, (str(org_id),))
        return [dict(row) for row in cur.fetchall()]
    
    @staticmethod
    def update(db: Database, user_id: UUID, data) -> Optional[Dict]:
        """Update user - accepts UserUpdate object or dict"""
        cur = db.cursor()

        # Handle both dict and object input
        def get_val(key):
            if isinstance(data, dict):
                return data.get(key)
            return getattr(data, key, None)

        updates = []
        values = []

        # All supported fields
        field_mappings = [
            ('first_name', 'first_name'),
            ('last_name', 'last_name'),
            ('email', 'email'),
            ('phone', 'phone'),
            ('title', 'title'),
            ('department', 'department'),
            ('linkedin', 'linkedin'),
            ('avatar_url', 'avatar_url'),
            ('password_hash', 'password_hash'),
        ]

        for field_name, column_name in field_mappings:
            val = get_val(field_name)
            if val is not None:
                updates.append(f"{column_name} = %s")
                values.append(val)

        # Always update updated_at
        updates.append("updated_at = NOW()")

        if len(updates) == 1:  # Only updated_at, no real changes
            return UserCRUD.get_by_id(db, user_id)

        values.append(str(user_id))
        cur.execute(f"""
            UPDATE users SET {', '.join(updates)}
            WHERE id = %s
            RETURNING id, organization_id, email, first_name, last_name, phone, role,
                      is_active, is_email_verified, last_login_at, created_at,
                      title, department, linkedin, avatar_url, updated_at
        """, values)

        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def update_login(db: Database, user_id: UUID):
        """Update last login timestamp"""
        cur = db.cursor()
        cur.execute("""
            UPDATE users SET last_login_at = NOW()
            WHERE id = %s
        """, (str(user_id),))
        db.commit()
    
    @staticmethod
    def verify_email(db: Database, user_id: UUID):
        """Mark user email as verified"""
        cur = db.cursor()
        cur.execute("""
            UPDATE users SET is_email_verified = TRUE, email_verified_at = NOW()
            WHERE id = %s
        """, (str(user_id),))
        db.commit()
    
    @staticmethod
    def deactivate(db: Database, user_id: UUID):
        """Deactivate user"""
        cur = db.cursor()
        cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (str(user_id),))
        db.commit()


# ==========================================
# Watchlist CRUD
# ==========================================

class WatchlistCRUD:
    
    @staticmethod
    def create(db: Database, org_id: UUID, user_id: UUID, data: WatchlistItemCreate) -> Dict:
        """Create watchlist item"""
        cur = db.cursor()
        
        # Check limits
        within_limit, _, _ = OrganizationCRUD.check_limits(db, org_id, "watchlist")
        if not within_limit:
            raise ValueError("Organization has reached maximum watchlist items limit")
        
        item_id = uuid4()
        
        # Get alert_frequency, defaulting to 'daily' if not provided
        alert_freq = getattr(data, 'alert_frequency', None)
        if alert_freq:
            alert_freq = alert_freq.value if hasattr(alert_freq, 'value') else alert_freq
        else:
            alert_freq = 'daily'

        # Get application number and bulletin number if provided
        app_no = getattr(data, 'application_no', None)
        bulletin_no = getattr(data, 'bulletin_no', None)

        logger.info(f"Creating watchlist item: brand={data.brand_name}, app_no={app_no}, bulletin_no={bulletin_no}")

        cur.execute("""
            INSERT INTO watchlist_mt (
                id, organization_id, user_id, brand_name, nice_class_numbers, description,
                alert_threshold, monitor_similar_names, monitor_similar_logos, alert_frequency,
                customer_application_no, customer_bulletin_no
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            str(item_id), str(org_id), str(user_id), data.brand_name, data.nice_class_numbers,
            data.description, data.similarity_threshold, data.monitor_text, data.monitor_visual,
            alert_freq, app_no, bulletin_no
        ))
        
        db.commit()
        return dict(cur.fetchone())
    
    @staticmethod
    def get_by_id(db: Database, item_id: UUID, org_id: Optional[UUID] = None) -> Optional[Dict]:
        """Get watchlist item by ID"""
        cur = db.cursor()
        query = "SELECT * FROM watchlist_mt WHERE id = %s"
        params = [str(item_id)]
        
        if org_id:
            query += " AND organization_id = %s"
            params.append(str(org_id))
        
        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def get_by_organization(
        db: Database, 
        org_id: UUID, 
        active_only: bool = True,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[Dict], int]:
        """Get watchlist items for organization with pagination"""
        cur = db.cursor()
        
        # Count total
        count_query = "SELECT COUNT(*) FROM watchlist_mt WHERE organization_id = %s"
        if active_only:
            count_query += " AND is_active = TRUE"
        cur.execute(count_query, (str(org_id),))
        total = cur.fetchone()['count']
        
        # Get items with alert counts
        query = """
            SELECT w.*,
                   COUNT(a.id) FILTER (WHERE a.status = 'new') AS new_alerts_count,
                   COUNT(a.id) AS total_alerts_count
            FROM watchlist_mt w
            LEFT JOIN alerts_mt a ON w.id = a.watchlist_item_id
            WHERE w.organization_id = %s
        """
        if active_only:
            query += " AND w.is_active = TRUE"
        
        query += """
            GROUP BY w.id
            ORDER BY w.created_at DESC
            LIMIT %s OFFSET %s
        """
        
        offset = (page - 1) * page_size
        cur.execute(query, (str(org_id), page_size, offset))
        
        return [dict(row) for row in cur.fetchall()], total
    
    @staticmethod
    def get_all_active(db: Database) -> List[Dict]:
        """Get all active watchlist items across all organizations (for scanning)"""
        cur = db.cursor()
        cur.execute("""
            SELECT w.*, o.name as org_name
            FROM watchlist_mt w
            JOIN organizations o ON w.organization_id = o.id
            WHERE w.is_active = TRUE AND o.is_active = TRUE
            ORDER BY w.organization_id, w.id
        """)
        return [dict(row) for row in cur.fetchall()]
    
    @staticmethod
    def update(db: Database, item_id: UUID, org_id: UUID, data: WatchlistItemUpdate) -> Optional[Dict]:
        """Update watchlist item"""
        cur = db.cursor()

        # Map schema field names to database column names
        field_mapping = {
            'application_no': 'customer_application_no',
            'bulletin_no': 'customer_bulletin_no',
            'registration_no': 'customer_registration_no',
        }

        updates = []
        values = []

        for field, value in data.dict(exclude_unset=True).items():
            if value is not None:
                if field == 'alert_frequency':
                    value = value.value
                # Map field name to DB column if needed
                db_field = field_mapping.get(field, field)
                updates.append(f"{db_field} = %s")
                values.append(value)

        if not updates:
            return WatchlistCRUD.get_by_id(db, item_id, org_id)
        
        values.extend([str(item_id), str(org_id)])
        cur.execute(f"""
            UPDATE watchlist_mt SET {', '.join(updates)}
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """, values)
        
        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def delete(db: Database, item_id: UUID, org_id: UUID) -> bool:
        """Soft delete watchlist item"""
        cur = db.cursor()
        cur.execute("""
            UPDATE watchlist_mt SET is_active = FALSE
            WHERE id = %s AND organization_id = %s
        """, (str(item_id), str(org_id)))
        db.commit()
        return cur.rowcount > 0
    
    @staticmethod
    def update_embedding(
        db: Database,
        item_id: UUID,
        text_embedding: List[float],
        logo_embedding: Optional[List[float]] = None,
        logo_ocr_text: Optional[str] = None,
    ):
        """Update watchlist item embeddings and OCR text"""
        cur = db.cursor()

        if logo_embedding:
            cur.execute("""
                UPDATE watchlist_mt
                SET text_embedding = %s::halfvec,
                    logo_embedding = %s::halfvec,
                    logo_ocr_text = COALESCE(%s, logo_ocr_text)
                WHERE id = %s
            """, (str(text_embedding), str(logo_embedding), logo_ocr_text, str(item_id)))
        else:
            cur.execute("""
                UPDATE watchlist_mt
                SET text_embedding = %s::halfvec,
                    logo_ocr_text = COALESCE(%s, logo_ocr_text)
                WHERE id = %s
            """, (str(text_embedding), logo_ocr_text, str(item_id)))

        db.commit()

    @staticmethod
    def update_logo(
        db: Database,
        item_id: UUID,
        logo_path: str,
        logo_embedding: Optional[List[float]] = None,
        dino_embedding: Optional[List[float]] = None,
        color_histogram: Optional[List[float]] = None,
        logo_ocr_text: Optional[str] = None,
    ):
        """Update watchlist item logo path and all visual embeddings"""
        cur = db.cursor()

        sets = ["logo_path = %s"]
        vals = [logo_path]

        if logo_embedding is not None:
            sets.append("logo_embedding = %s::halfvec")
            vals.append(str(logo_embedding))
        if dino_embedding is not None:
            sets.append("logo_dinov2_embedding = %s::halfvec")
            vals.append(str(dino_embedding))
        if color_histogram is not None:
            sets.append("logo_color_histogram = %s::halfvec")
            vals.append(str(color_histogram))
        if logo_ocr_text is not None:
            sets.append("logo_ocr_text = %s")
            vals.append(logo_ocr_text)

        vals.append(str(item_id))
        cur.execute(
            f"UPDATE watchlist_mt SET {', '.join(sets)} WHERE id = %s",
            vals
        )
        db.commit()

    @staticmethod
    def clear_logo(db: Database, item_id: UUID):
        """Remove logo and all visual embeddings from watchlist item"""
        cur = db.cursor()
        cur.execute("""
            UPDATE watchlist_mt
            SET logo_path = NULL,
                logo_embedding = NULL,
                logo_dinov2_embedding = NULL,
                logo_color_histogram = NULL,
                logo_ocr_text = NULL
            WHERE id = %s
        """, (str(item_id),))
        db.commit()
    
    @staticmethod
    def update_scanned(db: Database, item_id: UUID):
        """Mark watchlist item as scanned"""
        cur = db.cursor()
        cur.execute("""
            UPDATE watchlist_mt SET last_scan_at = NOW()
            WHERE id = %s
        """, (str(item_id),))
        db.commit()


# ==========================================
# Alert CRUD
# ==========================================

class AlertCRUD:
    
    @staticmethod
    def create(
        db: Database,
        org_id: UUID,
        watchlist_id: UUID,
        conflicting_trademark: Dict,
        scores: Dict,
        source_info: Dict,
        user_id: UUID = None,
        overlapping_classes: List[int] = None
    ) -> Dict:
        """Create new alert

        Args:
            overlapping_classes: List of Nice classes that overlap between watchlist and conflict
        """
        cur = db.cursor()

        # Get user_id from watchlist item if not provided
        if not user_id:
            cur.execute("SELECT user_id FROM watchlist_mt WHERE id = %s", (str(watchlist_id),))
            row = cur.fetchone()
            if row:
                user_id = UUID(row['user_id'])

        # Calculate severity using centralized thresholds
        from risk_engine import get_risk_level
        similarity_score = scores.get('total', 0)
        severity = get_risk_level(similarity_score)

        # Fetch appeal_deadline from the conflicting trademark
        opposition_deadline = None
        conflict_id = conflicting_trademark.get('id')
        if conflict_id:
            cur.execute(
                "SELECT appeal_deadline FROM trademarks WHERE id = %s::uuid",
                (str(conflict_id),)
            )
            row = cur.fetchone()
            if row and row.get('appeal_deadline'):
                opposition_deadline = row['appeal_deadline']

        alert_id = uuid4()

        cur.execute("""
            INSERT INTO alerts_mt (
                id, user_id, organization_id, watchlist_item_id, conflicting_trademark_id,
                conflicting_name, conflicting_application_no,
                conflicting_classes, conflicting_holder_name, conflicting_image_path,
                overall_risk_score, text_similarity_score, semantic_similarity_score,
                visual_similarity_score, translation_similarity_score,
                phonetic_match, severity, source_type, alert_type, status,
                overlapping_classes, opposition_deadline
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            str(alert_id), str(user_id) if user_id else None, str(org_id), str(watchlist_id),
            str(conflict_id) if conflict_id else None,
            conflicting_trademark.get('name'),
            conflicting_trademark.get('application_no'),
            conflicting_trademark.get('classes', []),
            conflicting_trademark.get('holder'),
            conflicting_trademark.get('image_path'),
            similarity_score,
            scores.get('text_similarity'),
            scores.get('semantic_similarity'),
            scores.get('visual_similarity'),
            scores.get('translation_similarity', 0),
            scores.get('phonetic_match', False),
            severity,
            source_info.get('type'),
            'similarity',  # alert_type
            'new',  # status
            overlapping_classes or [],
            opposition_deadline
        ))
        
        db.commit()
        return dict(cur.fetchone())
    
    @staticmethod
    def get_by_id(db: Database, alert_id: UUID, org_id: Optional[UUID] = None) -> Optional[Dict]:
        """Get alert by ID with appeal deadline from trademarks join"""
        cur = db.cursor()
        query = """
            SELECT a.*,
                   t.appeal_deadline as conflict_appeal_deadline,
                   t.bulletin_date as conflict_bulletin_date,
                   t.bulletin_no as conflict_bulletin_no,
                   t.current_status as conflict_live_status,
                   t.nice_class_numbers as conflict_live_classes
            FROM alerts_mt a
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.id = %s
        """
        params = [str(alert_id)]

        if org_id:
            query += " AND a.organization_id = %s"
            params.append(str(org_id))

        cur.execute(query, params)
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def get_by_organization(
        db: Database,
        org_id: UUID,
        status: Optional[List[str]] = None,
        severity: Optional[List[str]] = None,
        watchlist_id: Optional[UUID] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Tuple[List[Dict], int]:
        """Get alerts for organization with filtering"""
        cur = db.cursor()
        
        conditions = ["organization_id = %s"]
        params = [str(org_id)]
        
        if status:
            conditions.append(f"status = ANY(%s)")
            params.append(status)
        
        if severity:
            conditions.append(f"severity = ANY(%s)")
            params.append(severity)
        
        if watchlist_id:
            conditions.append("watchlist_item_id = %s")
            params.append(str(watchlist_id))
        
        where_clause = " AND ".join(conditions)
        
        # Count total
        cur.execute(f"SELECT COUNT(*) FROM alerts_mt WHERE {where_clause}", params)
        total = cur.fetchone()['count']
        
        # Get alerts with watchlist info and both bulletin numbers
        # Also fetch live status from trademarks table since alerts_mt.conflicting_status may be NULL
        offset = (page - 1) * page_size

        cur.execute(f"""
            SELECT a.*,
                   w.brand_name as watched_brand_name,
                   w.customer_bulletin_no as watchlist_bulletin_no,
                   w.customer_application_no as watchlist_application_no,
                   w.nice_class_numbers as watchlist_classes,
                   t.bulletin_no as conflict_bulletin_no,
                   t.current_status as conflict_live_status,
                   t.nice_class_numbers as conflict_live_classes,
                   t.appeal_deadline as conflict_appeal_deadline,
                   t.bulletin_date as conflict_bulletin_date,
                   (t.extracted_goods IS NOT NULL
                       AND t.extracted_goods != '[]'::jsonb
                       AND t.extracted_goods != 'null'::jsonb) AS conflict_has_extracted_goods
            FROM alerts_mt a
            LEFT JOIN watchlist_mt w ON a.watchlist_item_id = w.id
            LEFT JOIN trademarks t ON a.conflicting_trademark_id = t.id
            WHERE a.organization_id = %s
            {"AND a.status = ANY(%s)" if status else ""}
            {"AND a.severity = ANY(%s)" if severity else ""}
            {"AND a.watchlist_item_id = %s" if watchlist_id else ""}
            ORDER BY a.created_at DESC
            LIMIT %s OFFSET %s
        """, [p for p in [str(org_id)] +
              ([status] if status else []) +
              ([severity] if severity else []) +
              ([str(watchlist_id)] if watchlist_id else []) +
              [page_size, offset]])
        
        return [dict(row) for row in cur.fetchall()], total
    
    @staticmethod
    def update_status(
        db: Database,
        alert_id: UUID,
        org_id: UUID,
        status: AlertStatus,
        user_id: Optional[UUID] = None,
        notes: Optional[str] = None
    ) -> Optional[Dict]:
        """Update alert status"""
        cur = db.cursor()
        
        updates = ["status = %s"]
        values = [status.value]
        
        now = datetime.utcnow()
        
        if status == AlertStatus.SEEN:
            updates.append("seen_at = %s")
            values.append(now)
        elif status == AlertStatus.ACKNOWLEDGED:
            updates.append("acknowledged_at = %s")
            updates.append("acknowledged_by = %s")
            values.extend([now, str(user_id) if user_id else None])
        elif status in [AlertStatus.RESOLVED, AlertStatus.DISMISSED]:
            updates.append("resolved_at = %s")
            updates.append("resolved_by = %s")
            if notes:
                updates.append("resolution_notes = %s")
                values.extend([now, str(user_id) if user_id else None, notes])
            else:
                values.extend([now, str(user_id) if user_id else None])
        
        values.extend([str(alert_id), str(org_id)])
        
        cur.execute(f"""
            UPDATE alerts_mt SET {', '.join(updates)}
            WHERE id = %s AND organization_id = %s
            RETURNING *
        """, values)
        
        db.commit()
        row = cur.fetchone()
        return dict(row) if row else None
    
    @staticmethod
    def mark_notified(db: Database, alert_id: UUID, channel: str):
        """Mark alert as notified via channel"""
        cur = db.cursor()
        
        if channel == 'email':
            cur.execute("""
                UPDATE alerts_mt SET email_sent = TRUE, email_sent_at = NOW()
                WHERE id = %s
            """, (str(alert_id),))
        elif channel == 'webhook':
            cur.execute("""
                UPDATE alerts_mt SET webhook_sent = TRUE, webhook_sent_at = NOW()
                WHERE id = %s
            """, (str(alert_id),))
        
        db.commit()
    
    @staticmethod
    def get_pending_notifications(db: Database, channel: str, frequency: str) -> List[Dict]:
        """Get alerts pending notification"""
        cur = db.cursor()
        
        if channel == 'email':
            cur.execute("""
                SELECT a.*, w.brand_name, w.notify_email, w.notification_frequency,
                       u.email as user_email, u.first_name
                FROM alerts_mt a
                JOIN watchlist_mt w ON a.watchlist_item_id = w.id
                JOIN users u ON w.user_id = u.id
                WHERE a.email_sent = FALSE
                  AND w.notify_email = TRUE
                  AND w.notification_frequency = %s
                  AND a.status = 'new'
                ORDER BY a.organization_id, a.created_at
            """, (frequency,))
        
        return [dict(row) for row in cur.fetchall()]
    
    @staticmethod
    def check_duplicate(
        db: Database,
        watchlist_id: UUID,
        conflicting_app_no: str
    ) -> bool:
        """Check if alert already exists for this combination"""
        cur = db.cursor()
        cur.execute("""
            SELECT id FROM alerts_mt
            WHERE watchlist_item_id = %s AND conflicting_application_no = %s
            AND status NOT IN ('resolved', 'dismissed')
        """, (str(watchlist_id), conflicting_app_no))
        return cur.fetchone() is not None


# ==========================================
# Scan Log CRUD
# ==========================================

class ScanLogCRUD:

    @staticmethod
    def create(db: Database, source_type: str, source_reference: str) -> UUID:
        """Create scan job entry"""
        cur = db.cursor()
        scan_id = uuid4()

        cur.execute("""
            INSERT INTO scan_jobs (id, job_type, source_folder, status, started_at, created_at)
            VALUES (%s, %s, %s, 'running', NOW(), NOW())
            RETURNING id
        """, (str(scan_id), source_type, source_reference))

        db.commit()
        return scan_id

    @staticmethod
    def complete(
        db: Database,
        scan_id: UUID,
        trademarks_scanned: int,
        watchlist_checked: int,
        alerts_generated: int
    ):
        """Mark scan as complete"""
        cur = db.cursor()
        cur.execute("""
            UPDATE scan_jobs SET
                status = 'completed',
                completed_at = NOW(),
                total_trademarks_scanned = %s,
                total_watchlist_items_checked = %s,
                total_alerts_generated = %s
            WHERE id = %s
        """, (trademarks_scanned, watchlist_checked, alerts_generated, str(scan_id)))
        db.commit()

    @staticmethod
    def fail(db: Database, scan_id: UUID, error_message: str):
        """Mark scan as failed"""
        cur = db.cursor()
        cur.execute("""
            UPDATE scan_jobs SET
                status = 'failed',
                completed_at = NOW(),
                error_message = %s
            WHERE id = %s
        """, (error_message, str(scan_id)))
        db.commit()

    @staticmethod
    def get_last_scan(db: Database, source_type: str) -> Optional[Dict]:
        """Get last successful scan for source type"""
        cur = db.cursor()
        cur.execute("""
            SELECT * FROM scan_jobs
            WHERE job_type = %s AND status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
        """, (source_type,))
        row = cur.fetchone()
        return dict(row) if row else None
