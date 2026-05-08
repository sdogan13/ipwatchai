-- ============================================
-- Tasarım (Industrial Design) Schema Migration
-- Adds:
--   * design_status enum
--   * locarno_classes_lookup (32 top-level classes)
--   * designs (main table; reuses existing holders for applicants)
--   * design_views (per-view embeddings)
--   * design_events (mirror of trademark_events shape)
-- Idempotent: safe to run multiple times.
-- ============================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================
-- 1. Status enum
-- ============================================
DO $$ BEGIN
    CREATE TYPE design_status AS ENUM (
        'Yayında',
        'Tescil Edildi',
        'Hükümsüz',
        'Yenilendi',
        'Süresi Doldu',
        'Devredildi',
        'İptal Edildi',
        'Yayım Ertelendi',
        'Bilinmiyor'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================
-- 2. Locarno reference table (32 top-level classes)
-- ============================================
CREATE TABLE IF NOT EXISTS locarno_classes_lookup (
    class_number VARCHAR(2) PRIMARY KEY,
    name_tr      VARCHAR(500),
    name_en      VARCHAR(500),
    description  TEXT,
    updated_at   TIMESTAMP DEFAULT NOW()
);

INSERT INTO locarno_classes_lookup (class_number, name_tr, name_en) VALUES
 ('01', 'Gıda maddeleri', 'Foodstuffs'),
 ('02', 'Giyim eşyaları ve tuhafiye', 'Articles of clothing and haberdashery'),
 ('03', 'Seyahat eşyaları, çantalar, şemsiyeler ve kişisel eşyalar', 'Travel goods, cases, parasols and personal belongings'),
 ('04', 'Fırça malzemeleri', 'Brushware'),
 ('05', 'Tekstil parça malları, yapay ve doğal levha malzemeleri', 'Textile piecegoods, artificial and natural sheet material'),
 ('06', 'Mobilyalar', 'Furnishing'),
 ('07', 'Ev eşyaları (başka bir sınıfa girmeyenler)', 'Household goods, not elsewhere specified'),
 ('08', 'Aletler ve donanım', 'Tools and hardware'),
 ('09', 'Mal taşıma veya elleçleme için ambalajlar ve kaplar', 'Packages and containers for the transport or handling of goods'),
 ('10', 'Saatler ve diğer ölçme, kontrol ve sinyal cihazları', 'Clocks, watches, measuring, checking and signalling instruments'),
 ('11', 'Süs eşyaları', 'Articles of adornment'),
 ('12', 'Ulaşım veya kaldırma araçları', 'Means of transport or hoisting'),
 ('13', 'Elektrik üretim, dağıtım ve dönüşüm ekipmanları', 'Equipment for production, distribution or transformation of electricity'),
 ('14', 'Kayıt, telekomünikasyon veya bilgi işleme ekipmanları', 'Recording, telecommunication or information processing equipment'),
 ('15', 'Makineler (başka bir sınıfa girmeyenler)', 'Machines, not elsewhere specified'),
 ('16', 'Fotoğraf, sinema ve optik cihazlar', 'Photographic, cinematographic and optical apparatus'),
 ('17', 'Müzik aletleri', 'Musical instruments'),
 ('18', 'Baskı ve büro makineleri', 'Printing and office machinery'),
 ('19', 'Kırtasiye, büro, sanat ve öğretim malzemeleri', 'Stationery and office equipment, artists and teaching materials'),
 ('20', 'Satış ve reklam ekipmanları, işaretler', 'Sales and advertising equipment, signs'),
 ('21', 'Oyunlar, oyuncaklar, çadırlar ve spor malzemeleri', 'Games, toys, tents and sports goods'),
 ('22', 'Silahlar, piroteknik ürünler, av, balıkçılık ve haşere mücadele eşyaları', 'Arms, pyrotechnic articles, articles for hunting, fishing and pest killing'),
 ('23', 'Sıvı dağıtım, sıhhi tesisat, ısıtma, havalandırma ve klima ekipmanları', 'Fluid distribution equipment, sanitary, heating, ventilation and air-conditioning equipment'),
 ('24', 'Tıbbi ve laboratuvar ekipmanları', 'Medical and laboratory equipment'),
 ('25', 'Bina elemanları ve inşaat unsurları', 'Building units and construction elements'),
 ('26', 'Aydınlatma cihazları', 'Lighting apparatus'),
 ('27', 'Tütün ve sigara malzemeleri', 'Tobacco and smokers supplies'),
 ('28', 'Eczacılık ve kozmetik ürünleri, tuvalet eşyaları', 'Pharmaceutical and cosmetic products, toilet articles'),
 ('29', 'Yangın, kaza önleme ve kurtarma için cihazlar', 'Devices and equipment against fire hazards, for accident prevention and rescue'),
 ('30', 'Hayvan bakım ve kullanım eşyaları', 'Articles for the care and handling of animals'),
 ('31', 'Yiyecek veya içecek hazırlama makine ve cihazları', 'Machines and appliances for preparing food or drink'),
 ('32', 'Grafik semboller ve logolar, yüzey desenleri, süslemeler', 'Graphic symbols and logos, surface patterns, ornamentation')
ON CONFLICT (class_number) DO NOTHING;

-- ============================================
-- 3. designs (main table)
-- ============================================
CREATE TABLE IF NOT EXISTS designs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    -- Registry discriminator (stable internal identifier; UI-facing labels live in i18n)
    registry_type        VARCHAR(20) NOT NULL DEFAULT 'design'
                         CHECK (registry_type IN ('trademark', 'design')),
    -- Natural keys
    application_no       VARCHAR(50),
    design_index         INTEGER NOT NULL DEFAULT 1,
    registration_no      VARCHAR(50),
    -- Section + lifecycle
    section              VARCHAR(20) NOT NULL,
    current_status       design_status DEFAULT 'Yayında',
    effective_status     design_status,
    final_status         design_status,
    final_status_at      DATE,
    final_status_source  VARCHAR(10),
    -- Dates
    application_date     DATE,
    filing_date          DATE,
    registration_date    DATE,
    bulletin_no          VARCHAR(10),
    bulletin_date        DATE,
    opposition_end       DATE,
    -- Content
    product_name_tr      VARCHAR(500),
    product_name_en      VARCHAR(500),
    locarno_classes      TEXT[],
    design_count         INTEGER DEFAULT 1,
    -- People
    holder_id            UUID REFERENCES holders(id) ON DELETE SET NULL,
    designers            TEXT[],
    attorney_name        TEXT,
    attorney_firm        TEXT,
    -- Structured optionals
    priorities           JSONB DEFAULT '[]'::jsonb,
    hague_reference      JSONB,
    deferred_publication JSONB,
    -- Aggregate visual embeddings
    dinov2_vitl14_mean   halfvec(1024),
    clip_vitb32_mean     halfvec(512),
    -- Provenance
    source_issue_folder  VARCHAR(255),
    page_range_start     INTEGER,
    page_range_end       INTEGER,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Natural unique constraint covering both TR (application_no, design_index) and Hague (registration_no).
-- We use a partial unique index so Hague-only rows (with NULL application_no) are deduped on registration_no instead.
-- Backfill registry_type column for environments where designs already exists
-- without it. ADD COLUMN IF NOT EXISTS makes this safe to re-run.
ALTER TABLE designs
    ADD COLUMN IF NOT EXISTS registry_type VARCHAR(20) NOT NULL DEFAULT 'design';
DO $$ BEGIN
    ALTER TABLE designs
        ADD CONSTRAINT designs_registry_type_check
        CHECK (registry_type IN ('trademark', 'design'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_designs_tr_natural
    ON designs (application_no, design_index, section)
    WHERE application_no IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_designs_hague_natural
    ON designs (registration_no, section)
    WHERE application_no IS NULL AND registration_no IS NOT NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_des_app_no ON designs(application_no);
CREATE INDEX IF NOT EXISTS idx_des_reg_no ON designs(registration_no);
CREATE INDEX IF NOT EXISTS idx_des_status ON designs(current_status);
CREATE INDEX IF NOT EXISTS idx_des_section ON designs(section);
CREATE INDEX IF NOT EXISTS idx_des_holder ON designs(holder_id);
CREATE INDEX IF NOT EXISTS idx_des_locarno_arr ON designs USING GIN (locarno_classes);
CREATE INDEX IF NOT EXISTS idx_des_designers_arr ON designs USING GIN (designers);
CREATE INDEX IF NOT EXISTS idx_des_application_date ON designs(application_date DESC);
CREATE INDEX IF NOT EXISTS idx_des_bulletin_date ON designs(bulletin_date DESC);
CREATE INDEX IF NOT EXISTS idx_des_product_trgm ON designs USING GIST (product_name_tr gist_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_des_dinov2_vec ON designs USING hnsw (dinov2_vitl14_mean halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE dinov2_vitl14_mean IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_des_clip_vec ON designs USING hnsw (clip_vitb32_mean halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE clip_vitb32_mean IS NOT NULL;

-- ============================================
-- 4. design_views (per-view embeddings)
-- ============================================
CREATE TABLE IF NOT EXISTS design_views (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    design_id UUID NOT NULL REFERENCES designs(id) ON DELETE CASCADE,
    view_index INTEGER NOT NULL,
    page INTEGER,
    image_xref INTEGER,
    bbox NUMERIC[],
    image_path TEXT,
    dinov2_vitl14 halfvec(1024),
    clip_vitb32   halfvec(512),
    color_hsv     halfvec(512),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_design_view ON design_views (design_id, view_index);
CREATE INDEX IF NOT EXISTS idx_dv_design ON design_views(design_id);
CREATE INDEX IF NOT EXISTS idx_dv_dinov2_vec ON design_views USING hnsw (dinov2_vitl14 halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE dinov2_vitl14 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dv_clip_vec ON design_views USING hnsw (clip_vitb32 halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE clip_vitb32 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_dv_color_vec ON design_views USING hnsw (color_hsv halfvec_cosine_ops)
    WITH (m=16, ef_construction=200) WHERE color_hsv IS NOT NULL;

-- ============================================
-- 5. design_events (mirror of trademark_events)
-- ============================================
CREATE TABLE IF NOT EXISTS design_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    design_id UUID REFERENCES designs(id) ON DELETE SET NULL,
    application_no  VARCHAR(50),
    registration_no VARCHAR(50),
    event_type      VARCHAR(50) NOT NULL,
    event_date      DATE,
    bulletin_no     VARCHAR(10),
    bulletin_date   DATE,
    page            INTEGER,
    details         JSONB DEFAULT '{}'::jsonb,
    free_text       TEXT,
    event_fingerprint VARCHAR(64) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_design_event ON design_events(event_fingerprint);
CREATE INDEX IF NOT EXISTS idx_de_app_no ON design_events(application_no);
CREATE INDEX IF NOT EXISTS idx_de_reg_no ON design_events(registration_no) WHERE registration_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_de_type ON design_events(event_type);
CREATE INDEX IF NOT EXISTS idx_de_design ON design_events(design_id) WHERE design_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_de_bulletin_date ON design_events(bulletin_date);
