import psycopg2, os
from psycopg2.extras import RealDictCursor

def check_missing_record():
    try:
        conn = psycopg2.connect(
            host='postgres',
            port=5432,
            database='trademark_db',
            user='turk_patent',
            password=os.getenv('DB_PASSWORD')
        )
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Keyword search with Turkish characters
        print("Searching for 'doğanpatent'...")
        cur.execute("SELECT name FROM trademarks WHERE name ILIKE '%doğanpatent%'")
        rows = cur.fetchall()
        for row in rows:
            print(f"NAME: {row['name']}")

        print("\nSearching for 'danışmanlık'...")
        cur.execute("SELECT name FROM trademarks WHERE name ILIKE '%danışmanlık%' LIMIT 50")
        rows = cur.fetchall()
        for row in rows:
            print(f"NAME: {row['name']}")
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_missing_record()
