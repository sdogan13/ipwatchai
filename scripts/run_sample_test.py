"""Test embedding generation on 100 sample records from BLT_485."""
import time, sys, os, json

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

os.environ['ENVIRONMENT'] = 'development'
os.environ['OCR_LANGUAGES'] = '["en","tr"]'

SAMPLE_SIZE = 100
FOLDER = r'C:\Users\701693\turk_patent\bulletins\Marka\BLT_485_2026-01-27'
METADATA = os.path.join(FOLDER, 'metadata.json')
BACKUP = os.path.join(FOLDER, 'metadata_backup.json')

def log(msg):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# Step 1: Create a sample metadata.json with only 100 records
log(f"Reading metadata from {FOLDER}...")
with open(METADATA, 'r', encoding='utf-8') as f:
    all_records = json.load(f)
log(f"Total records: {len(all_records)}")

# Pick 100 records that have images (NAME + IMAGE present)
sample = [r for r in all_records if r.get("TRADEMARK", {}).get("NAME") and r.get("IMAGE")][:SAMPLE_SIZE]
log(f"Sample size: {len(sample)} records with NAME + IMAGE")

# Back up original, write sample
log("Backing up original metadata.json...")
os.rename(METADATA, BACKUP)
with open(METADATA, 'w', encoding='utf-8') as f:
    json.dump(sample, f, ensure_ascii=False)

# Step 2: Load ai.py and process
try:
    log("Loading ai.py (all models)...")
    import importlib.util
    spec = importlib.util.spec_from_file_location('ai_mod', os.path.join(os.path.dirname(__file__), 'ai.py'))
    ai_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ai_mod)
    log("All models loaded.")

    from pathlib import Path
    t0 = time.time()
    log(f"Processing {len(sample)} sample records...")
    ai_mod.process_folder(Path(FOLDER))
    elapsed = time.time() - t0
    log(f"DONE in {elapsed:.1f}s ({elapsed/60:.1f} min)")

    # Step 3: Read back and verify
    with open(METADATA, 'r', encoding='utf-8') as f:
        processed = json.load(f)

    log(f"\n{'='*60}")
    log(f"VERIFICATION REPORT ({len(processed)} records)")
    log(f"{'='*60}")

    fields = ['text_embedding', 'image_embedding', 'dinov2_embedding',
              'color_histogram', 'logo_ocr_text', 'name_tr', 'detected_lang']

    for field in fields:
        count = sum(1 for r in processed if r.get(field) is not None)
        pct = count / len(processed) * 100
        log(f"  {field:<25} {count:>4}/{len(processed)} ({pct:.0f}%)")

    # Show sample translations
    log(f"\nSample translations (first 10):")
    for r in processed[:10]:
        name = r.get("TRADEMARK", {}).get("NAME", "?")
        tr = r.get("name_tr", "?")
        lang = r.get("detected_lang", "?")
        log(f"  {name:<30} -> TR: {tr:<30} (lang: {lang})")

    # Show sample OCR
    log(f"\nSample OCR (first 5 with text):")
    ocr_count = 0
    for r in processed:
        if r.get("logo_ocr_text") and ocr_count < 5:
            name = r.get("TRADEMARK", {}).get("NAME", "?")
            ocr = r.get("logo_ocr_text", "")[:60]
            log(f"  {name:<30} OCR: {ocr}")
            ocr_count += 1

    # Embedding dimensions
    for r in processed:
        if r.get("text_embedding"):
            log(f"\nEmbedding dimensions:")
            log(f"  text_embedding:    {len(r['text_embedding'])}d")
            if r.get("image_embedding"):
                log(f"  image_embedding:   {len(r['image_embedding'])}d")
            if r.get("dinov2_embedding"):
                log(f"  dinov2_embedding:  {len(r['dinov2_embedding'])}d")
            if r.get("color_histogram"):
                log(f"  color_histogram:   {len(r['color_histogram'])}d")
            break

finally:
    # Step 4: Restore original metadata.json
    log("\nRestoring original metadata.json...")
    if os.path.exists(BACKUP):
        if os.path.exists(METADATA):
            os.remove(METADATA)
        os.rename(BACKUP, METADATA)
        log("Original restored.")
    log("Test complete.")
