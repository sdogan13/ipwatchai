# IP WATCH AI - Database Schema

PostgreSQL 16 with pgvector extension for vector similarity search.

## Extensions

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";     -- UUID generation
CREATE EXTENSION IF NOT EXISTS "pg_trgm";       -- Trigram text similarity
CREATE EXTENSION IF NOT EXISTS "vector";        -- Vector similarity (pgvector)
CREATE EXTENSION IF NOT EXISTS "fuzzystrmatch"; -- Phonetic matching (dmetaphone)
```

---

## Core Tables

### trademarks

Main trademark records table.

```sql
CREATE TABLE trademarks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_no VARCHAR(255) UNIQUE NOT NULL,
    registration_no VARCHAR(255),
    wipo_no VARCHAR(255),
    current_status tm_status DEFAULT 'Published',
    holder_id UUID REFERENCES holders(id),
    name TEXT,
    nice_class_numbers INTEGER[],
    vienna_class_numbers INTEGER[],
    extracted_goods JSONB,
    image_path TEXT,
    bulletin_no VARCHAR(255),
    bulletin_date DATE,
    gazette_no VARCHAR(255),
    gazette_date DATE,

    -- AI Embeddings (halfvec for memory optimization)
    image_embedding halfvec(512),      -- CLIP ViT-B-32
    text_embedding halfvec(384),       -- MiniLM-L12
    dinov2_embedding halfvec(768),     -- DINOv2 ViT-B/14
    color_histogram halfvec(32),       -- Color distribution

    application_date DATE,
    registration_date DATE,
    expiry_date DATE,
    appeal_deadline DATE,
    last_event_date DATE,
    availability_status VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### Status Enum

```sql
CREATE TYPE tm_status AS ENUM (
    'Applied', 'Published', 'Opposed', 'Registered',
    'Refused', 'Withdrawn', 'Transferred', 'Renewed',
    'Partial Refusal', 'Expired', 'Unknown'
);
```

#### Indexes

```sql
-- Primary lookups
CREATE INDEX idx_tm_app_no ON trademarks(application_no);
CREATE INDEX idx_tm_status ON trademarks(current_status);

-- Text similarity (trigram)
CREATE INDEX idx_tm_name_trgm ON trademarks USING GIST (name gist_trgm_ops);

-- Phonetic similarity
CREATE INDEX idx_tm_phonetic ON trademarks (dmetaphone(name));

-- Array/JSONB indexes
CREATE INDEX idx_tm_nice_classes_arr ON trademarks USING GIN (nice_class_numbers);
CREATE INDEX idx_tm_extracted_goods ON trademarks USING GIN (extracted_goods);

-- Vector indexes (HNSW for fast ANN search)
CREATE INDEX idx_tm_image_vec ON trademarks
    USING hnsw (image_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200)
    WHERE image_embedding IS NOT NULL;

CREATE INDEX idx_tm_text_vec ON trademarks
    USING hnsw (text_embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 200);
```

---

### holders

Trademark holder/owner information.

```sql
CREATE TABLE holders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tpe_client_id VARCHAR(255) UNIQUE,
    name TEXT NOT NULL,
    address TEXT,
    city VARCHAR(255),
    country VARCHAR(255),
    postal_code VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_holders_name_trgm ON holders USING GIST (name gist_trgm_ops);
```

---

### nice_classes_lookup

Nice Classification reference data.

```sql
CREATE TABLE nice_classes_lookup (
    class_number INTEGER PRIMARY KEY,
    description TEXT,
    description_embedding halfvec(384),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

Classes 1-45 covering all trademark categories.

---

### watchlist

User watchlist for brand monitoring.

```sql
CREATE TABLE watchlist (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100) NOT NULL,
    brand_name TEXT NOT NULL,
    nice_class_numbers INTEGER[] NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

### alerts

Conflict alerts for watchlist items.

```sql
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id VARCHAR(100),
    watched_trademark_id UUID REFERENCES watchlist(id),
    conflicting_trademark_id UUID,
    risk_score FLOAT,
    status VARCHAR(20) DEFAULT 'Pending',
    immediate_sent BOOLEAN DEFAULT FALSE,
    reminder_sent BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

### trademark_history

Partitioned table for trademark timeline events.

```sql
CREATE TABLE trademark_history (
    id UUID DEFAULT uuid_generate_v4(),
    trademark_id UUID NOT NULL,
    event_date DATE NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    source_file VARCHAR(512),
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, event_date)
) PARTITION BY RANGE (event_date);

-- Partitions
CREATE TABLE trademark_history_legacy PARTITION OF trademark_history
    FOR VALUES FROM (MINVALUE) TO ('2023-01-01');
CREATE TABLE trademark_history_2024 PARTITION OF trademark_history
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE trademark_history_2025 PARTITION OF trademark_history
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE trademark_history_2026 PARTITION OF trademark_history
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
```

---

### processed_files

Ingestion tracking.

```sql
CREATE TABLE processed_files (
    filename VARCHAR(512) PRIMARY KEY,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) CHECK (status IN ('success', 'failed', 'processing')),
    record_count INT DEFAULT 0,
    error_log TEXT
);
```

---

## Views

### trademark_dashboard_view

Denormalized view for dashboard queries.

```sql
CREATE VIEW trademark_dashboard_view AS
SELECT
    t.id AS trademark_id,
    t.application_no,
    t.name AS trademark_name,
    t.current_status,
    t.image_path,
    h.name AS holder_name,
    h.city AS holder_city,
    t.nice_class_numbers,
    (
        SELECT jsonb_agg(jsonb_build_object(
            'class_number', ncl.class_number,
            'description', ncl.description
        ))
        FROM nice_classes_lookup ncl
        WHERE ncl.class_number = ANY(t.nice_class_numbers)
    ) AS nice_classes_details,
    t.application_date,
    t.registration_date,
    t.expiry_date,
    t.availability_status
FROM trademarks t
LEFT JOIN holders h ON t.holder_id = h.id;
```

---

## Multi-Tenant Schema (V3)

For SaaS deployment with tenant isolation.

### tenants

```sql
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    plan VARCHAR(50) DEFAULT 'free',
    settings JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### users (Multi-Tenant)

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES tenants(id),
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) DEFAULT 'user',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(tenant_id, email)
);
```

---

## Vector Search Queries

### Text Similarity Search

```sql
SELECT id, name, application_no,
       1 - (text_embedding <=> $1::halfvec) as similarity
FROM trademarks
WHERE text_embedding IS NOT NULL
ORDER BY text_embedding <=> $1::halfvec
LIMIT 50;
```

### Image Similarity Search

```sql
SELECT id, name, application_no, image_path,
       1 - (image_embedding <=> $1::halfvec) as similarity
FROM trademarks
WHERE image_embedding IS NOT NULL
  AND nice_class_numbers && $2::integer[]
ORDER BY image_embedding <=> $1::halfvec
LIMIT 50;
```

### Combined Hybrid Search

```sql
WITH text_results AS (
    SELECT id, 1 - (text_embedding <=> $1::halfvec) as text_sim
    FROM trademarks
    WHERE text_embedding IS NOT NULL
    ORDER BY text_embedding <=> $1::halfvec
    LIMIT 100
),
visual_results AS (
    SELECT id, 1 - (image_embedding <=> $2::halfvec) as visual_sim
    FROM trademarks
    WHERE image_embedding IS NOT NULL
    ORDER BY image_embedding <=> $2::halfvec
    LIMIT 100
)
SELECT t.*,
       COALESCE(tr.text_sim, 0) * 0.4 +
       COALESCE(vr.visual_sim, 0) * 0.4 +
       similarity(t.name, $3) * 0.2 as combined_score
FROM trademarks t
LEFT JOIN text_results tr ON t.id = tr.id
LEFT JOIN visual_results vr ON t.id = vr.id
WHERE tr.id IS NOT NULL OR vr.id IS NOT NULL
ORDER BY combined_score DESC
LIMIT 50;
```

---

## Trigram Text Search

```sql
-- Fuzzy name matching
SELECT id, name, similarity(name, 'NIKE') as sim
FROM trademarks
WHERE name % 'NIKE'
ORDER BY sim DESC
LIMIT 20;
```

---

## Phonetic Search

```sql
-- Sound-alike matching
SELECT id, name
FROM trademarks
WHERE dmetaphone(name) = dmetaphone('NIKEY')
LIMIT 20;
```

---

## Performance Configuration

```sql
-- Recommended PostgreSQL settings for vector workloads
SET shared_buffers = '16GB';
SET effective_cache_size = '48GB';
SET work_mem = '256MB';
SET maintenance_work_mem = '2GB';

-- pgvector specific
SET hnsw.ef_search = 100;  -- Higher = more accurate, slower
SET ivfflat.probes = 10;   -- For IVF indexes
```

---

## Backup & Maintenance

```sql
-- Vacuum and analyze for optimal performance
VACUUM ANALYZE trademarks;
VACUUM ANALYZE holders;

-- Reindex vectors after bulk inserts
REINDEX INDEX idx_tm_image_vec;
REINDEX INDEX idx_tm_text_vec;
```
