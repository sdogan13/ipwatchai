import psycopg2, os
from psycopg2.extras import RealDictCursor

def check_missing_record():
    conn = psycopg2.connect(
        host='localhost',
        port=5432,
        database='trademark_db',
        user='turk_patent',
        password='Dogan.1996'
    )
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query_name = "dogan patent"
    target_pattern = "%doganpatent%"

    print(f"Checking for records matching pattern: {target_pattern}")
    cur.execute("SELECT id, name FROM trademarks WHERE name ILIKE %s", (target_pattern,))
    rows = cur.fetchall()
    
    if not rows:
        print("No records found with ILIKE %doganpatent%")
        # Try a broader search
        cur.execute("SELECT id, name FROM trademarks WHERE name ILIKE '%dogan%' AND name ILIKE '%patent%' LIMIT 10")
        rows = cur.fetchall()

    for row in rows:
        print(f"ID: {row['id']} | Name: {row['name']}")
        
    cur.close()
    conn.close()

if __name__ == "__main__":
    check_missing_record()
