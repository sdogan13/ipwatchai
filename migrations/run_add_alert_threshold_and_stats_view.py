"""
Migration: Add default_alert_threshold column and org_dashboard_stats view

Fixes two pre-existing schema mismatches:
1. organizations.default_alert_threshold column (referenced by org settings endpoint)
2. org_dashboard_stats materialized view (referenced by org stats endpoint)
"""
import psycopg2

def run():
    conn = psycopg2.connect(
        host='127.0.0.1', port=5433,
        dbname='trademark_db', user='turk_patent', password='Dogan.1996'
    )
    conn.autocommit = True
    cur = conn.cursor()

    # 1. Add default_alert_threshold column if missing
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'organizations' AND column_name = 'default_alert_threshold'
    """)
    if not cur.fetchone():
        cur.execute("""
            ALTER TABLE organizations
            ADD COLUMN default_alert_threshold FLOAT DEFAULT 0.7
        """)
        print("Added column: organizations.default_alert_threshold (default 0.7)")
    else:
        print("Column already exists: organizations.default_alert_threshold")

    # 2. Create org_dashboard_stats view if missing
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_name = 'org_dashboard_stats'
    """)
    if not cur.fetchone():
        cur.execute("""
            CREATE OR REPLACE VIEW org_dashboard_stats AS
            SELECT
                o.id AS organization_id,
                o.name AS organization_name,
                COUNT(DISTINCT u.id) AS user_count,
                COUNT(DISTINCT w.id) FILTER (WHERE w.is_active = TRUE) AS active_watchlist_items,
                COUNT(DISTINCT a.id) FILTER (WHERE a.status = 'new') AS new_alerts,
                COUNT(DISTINCT a.id) FILTER (WHERE a.status NOT IN ('dismissed', 'resolved')) AS open_alerts,
                0 AS searches_this_month
            FROM organizations o
            LEFT JOIN users u ON u.organization_id = o.id AND u.is_active = TRUE
            LEFT JOIN watchlist_mt w ON w.organization_id = o.id
            LEFT JOIN alerts_mt a ON a.watchlist_item_id = w.id
            GROUP BY o.id, o.name
        """)
        print("Created view: org_dashboard_stats")
    else:
        print("View already exists: org_dashboard_stats")

    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    run()
