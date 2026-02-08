"""Add bulletin_no column to watchlist_mt table"""
import psycopg2

import os
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    database=os.getenv('DB_NAME', 'trademark_db'),
    user=os.getenv('DB_USER', 'turk_patent'),
    password=os.getenv('DB_PASSWORD')
)
cur = conn.cursor()

# Add bulletin_no column to watchlist_mt if not exists
cur.execute('''
    ALTER TABLE watchlist_mt
    ADD COLUMN IF NOT EXISTS customer_bulletin_no VARCHAR(50);
''')
conn.commit()
print('Column customer_bulletin_no added to watchlist_mt')

# Check if trademarks table has bulletin_no
cur.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'trademarks' AND column_name = 'bulletin_no'
""")
result = cur.fetchone()
print(f"Trademarks bulletin_no column: {'exists' if result else 'not found'}")

conn.close()
print('Done!')
