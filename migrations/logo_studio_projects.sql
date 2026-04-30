-- Logo Studio project threads and asynchronous visual audits
-- Usage: python migrations/run_logo_studio_projects_migration.py

CREATE TABLE IF NOT EXISTS logo_projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    org_id UUID REFERENCES organizations(id),
    user_id UUID REFERENCES users(id),
    brand_name TEXT NOT NULL,
    description TEXT DEFAULT '',
    style VARCHAR(50) DEFAULT 'modern',
    nice_classes INTEGER[] DEFAULT '{}',
    color_preferences TEXT DEFAULT '',
    selected_image_id UUID NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logo_projects_org ON logo_projects(org_id);
CREATE INDEX IF NOT EXISTS idx_logo_projects_user ON logo_projects(user_id);
CREATE INDEX IF NOT EXISTS idx_logo_projects_updated ON logo_projects(updated_at DESC);

ALTER TABLE generated_images
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES logo_projects(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS parent_image_id UUID REFERENCES generated_images(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS variant_index INTEGER,
    ADD COLUMN IF NOT EXISTS generation_kind VARCHAR(20) DEFAULT 'INITIAL',
    ADD COLUMN IF NOT EXISTS revision_prompt TEXT,
    ADD COLUMN IF NOT EXISTS audit_status VARCHAR(20) DEFAULT 'completed',
    ADD COLUMN IF NOT EXISTS audit_error TEXT,
    ADD COLUMN IF NOT EXISTS audited_at TIMESTAMP;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_logo_projects_selected_image'
    ) THEN
        ALTER TABLE logo_projects
            ADD CONSTRAINT fk_logo_projects_selected_image
            FOREIGN KEY (selected_image_id)
            REFERENCES generated_images(id)
            ON DELETE SET NULL
            NOT VALID;
    END IF;
END $$;

UPDATE generated_images
SET audit_status = 'completed',
    audited_at = COALESCE(audited_at, created_at)
WHERE audit_status IS NULL;

CREATE INDEX IF NOT EXISTS idx_generated_images_project ON generated_images(project_id);
CREATE INDEX IF NOT EXISTS idx_generated_images_parent ON generated_images(parent_image_id);
CREATE INDEX IF NOT EXISTS idx_generated_images_audit_status ON generated_images(audit_status);
