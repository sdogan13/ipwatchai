"""Promote servet4213@gmail.com and dogansibrahim@gmail.com to superadmin."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import psycopg2, psycopg2.extras, os

conn = psycopg2.connect(
    host='127.0.0.1', port=5433, dbname='trademark_db',
    user='turk_patent', password=os.getenv('DB_PASSWORD', 'Dogan.1996')
)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Get sdogan's org (the main superadmin org)
cur.execute("SELECT organization_id FROM users WHERE email = 'sdogan1334@gmail.com'")
main_org_id = cur.fetchone()['organization_id']
print(f"Main superadmin org_id: {main_org_id}")

# Get the new users' current orgs
emails = ['servet4213@gmail.com', 'dogansibrahim@gmail.com']
old_orgs = []
for email in emails:
    cur.execute('SELECT id, organization_id FROM users WHERE email = %s', (email,))
    u = cur.fetchone()
    if u:
        old_orgs.append(u['organization_id'])
        print(f"{email}: user_id={u['id']}, old_org={u['organization_id']}")
    else:
        print(f"{email}: NOT FOUND")

# Update both users: set superadmin, move to main org
cur.execute("""
    UPDATE users
    SET is_superadmin = true,
        role = 'owner',
        is_organization_admin = true,
        is_email_verified = true,
        organization_id = %s
    WHERE email IN ('servet4213@gmail.com', 'dogansibrahim@gmail.com')
    RETURNING email, role, is_superadmin, organization_id
""", (str(main_org_id),))
updated = cur.fetchall()
for u in updated:
    print(f"Updated: {u['email']} role={u['role']} is_superadmin={u['is_superadmin']}")

# Clean up empty orgs that were auto-created during registration
for org_id in old_orgs:
    if str(org_id) != str(main_org_id):
        cur.execute('SELECT COUNT(*) as cnt FROM users WHERE organization_id = %s', (str(org_id),))
        cnt = cur.fetchone()['cnt']
        if cnt == 0:
            cur.execute('DELETE FROM organizations WHERE id = %s', (str(org_id),))
            print(f"Deleted empty org: {org_id}")

conn.commit()

# Verify all superadmins
cur.execute("""
    SELECT u.email, u.first_name, u.last_name, u.role, u.is_superadmin, u.is_active, o.name as org_name
    FROM users u
    JOIN organizations o ON u.organization_id = o.id
    WHERE u.is_superadmin = true
    ORDER BY u.email
""")
rows = cur.fetchall()
print(f"\nAll superadmins ({len(rows)}):")
for r in rows:
    print(f"  {r['email']} | {r['first_name']} {r['last_name']} | role={r['role']} | org={r['org_name']} | active={r['is_active']}")

conn.close()
print("\nDone!")
