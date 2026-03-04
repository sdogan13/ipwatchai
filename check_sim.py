import psycopg2, os
from psycopg2.extras import RealDictCursor

def check_sim():
    try:
        conn = psycopg2.connect(
            host='postgres',
            port=5432,
            database='trademark_db',
            user='turk_patent',
            password=os.getenv('DB_PASSWORD')
        )
        cur = conn.cursor()

        q = 'dogan patent'
        # The exact name from the DB (with dots and Turkish chars)
        t_raw = 'd.r.p doğanpatent marka ve patent danışmanlık hizmetleri trademark&patent office'
        
        # Normalized name (as it would be in normalize_sql)
        t_norm = 'drp doganpatent marka ve patent danismanlik hizmetleri trademark patent office'

        cur.execute('SELECT similarity(%s, %s)', (q, t_raw))
        sim_raw = cur.fetchone()[0]
        
        cur.execute('SELECT similarity(%s, %s)', (q, t_norm))
        sim_norm = cur.fetchone()[0]
        
        print(f"Similarity (raw): {sim_raw}")
        print(f"Similarity (norm): {sim_norm}")
        
        # Check if individual tokens match
        cur.execute("SELECT %s ILIKE %s", (t_raw, '%dogan%'))
        has_dogan = cur.fetchone()[0]
        cur.execute("SELECT %s ILIKE %s", (t_raw, '%patent%'))
        has_patent = cur.fetchone()[0]
        
        print(f"Has dogan: {has_dogan}")
        print(f"Has patent: {has_patent}")

        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_sim()
