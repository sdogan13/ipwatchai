"""Standalone embedding generation for specific folders."""
import time, sys, os

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

os.environ['ENVIRONMENT'] = 'development'
os.environ['OCR_LANGUAGES'] = '["en","tr"]'

# Redirect output to log file
log_path = os.path.join(os.path.dirname(__file__), 'embedding_run.log')
log_file = open(log_path, 'w', encoding='utf-8')

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    log_file.write(line + '\n')
    log_file.flush()

log("Loading ai.py (all models)...")
import importlib.util
spec = importlib.util.spec_from_file_location('ai_mod', os.path.join(os.path.dirname(__file__), 'ai.py'))
ai_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai_mod)
log("All models loaded.")

from pathlib import Path

folders = [
    Path(r'C:\Users\701693\turk_patent\bulletins\Marka\BLT_485_2026-01-27'),
    Path(r'C:\Users\701693\turk_patent\bulletins\Marka\GZ_499_2026-01-30'),
]

for folder in folders:
    log(f"Starting {folder.name}...")
    t0 = time.time()
    try:
        ai_mod.process_folder(folder)
        elapsed = time.time() - t0
        log(f"DONE {folder.name} in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    except Exception as e:
        import traceback
        elapsed = time.time() - t0
        log(f"ERROR {folder.name} after {elapsed:.0f}s: {e}")
        log(traceback.format_exc())

log("All folders complete.")
log_file.close()
