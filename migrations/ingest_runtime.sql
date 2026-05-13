CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

DO $$
BEGIN
    CREATE TYPE tm_status AS ENUM (
        'Başvuruldu',
        'Yayında',
        'İtiraz Edildi',
        'Tescil Edildi',
        'Reddedildi',
        'Geri Çekildi',
        'Devredildi',
        'Yenilendi',
        'Kısmi Red',
        'Süresi Doldu',
        'Bilinmiyor',
        'İptal Edildi'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

ALTER TYPE tm_status ADD VALUE IF NOT EXISTS 'İptal Edildi';

CREATE TABLE IF NOT EXISTS processed_files (
    filename VARCHAR(512) PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'processing' CHECK (
        status IN ('processing', 'success', 'failed', 'repaired', 'unrecoverable', 'regen_failed')
    ),
    record_count INT DEFAULT 0,
    error_log TEXT
);

CREATE TABLE IF NOT EXISTS nice_classes_lookup (
    class_number INTEGER PRIMARY KEY,
    name_tr VARCHAR(100),
    name_en VARCHAR(100),
    description TEXT,
    description_embedding halfvec(384),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trademarks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_no VARCHAR(255) UNIQUE NOT NULL,
    name TEXT,
    current_status tm_status DEFAULT 'Yayında',
    last_event_date DATE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS availability_status VARCHAR(50);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS nice_class_numbers INTEGER[];
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS vienna_class_numbers INTEGER[];
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS extracted_goods JSONB;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS registration_no VARCHAR(255);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS wipo_no VARCHAR(255);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS application_date DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS registration_date DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS bulletin_no VARCHAR(255);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS bulletin_date DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS gazette_no VARCHAR(255);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS gazette_date DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS appeal_deadline DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS expiry_date DATE;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS image_path TEXT;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS image_embedding halfvec(512);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS dinov2_embedding halfvec(768);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS color_histogram halfvec(512);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS logo_ocr_text TEXT;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr VARCHAR(500);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS detected_lang VARCHAR(10);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr_backend VARCHAR(32);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr_model VARCHAR(255);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS name_tr_updated_at TIMESTAMP;
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS holder_name VARCHAR(500);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS holder_tpe_client_id VARCHAR(50);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS attorney_name VARCHAR(500);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS attorney_no VARCHAR(50);
ALTER TABLE trademarks ADD COLUMN IF NOT EXISTS status_source VARCHAR(10);

DROP INDEX IF EXISTS idx_tm_name;
CREATE INDEX IF NOT EXISTS idx_tm_name_trgm ON trademarks USING GIST (name gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_tm_holder_tpe_id ON trademarks(holder_tpe_client_id);
CREATE INDEX IF NOT EXISTS idx_tm_holder_name ON trademarks(holder_name);
CREATE INDEX IF NOT EXISTS idx_tm_attorney_name ON trademarks(attorney_name);
CREATE INDEX IF NOT EXISTS idx_tm_attorney_no ON trademarks(attorney_no);
