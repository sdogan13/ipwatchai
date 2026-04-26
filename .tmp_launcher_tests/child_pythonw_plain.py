from pathlib import Path
import traceback
out = Path(r'C:\Users\701693\turk_patent\.tmp_launcher_tests\pythonw_plain.txt')
try:
 import psycopg2
 out.write_text('ok', encoding='utf-8')
except Exception:
 out.write_text(traceback.format_exc(), encoding='utf-8')
