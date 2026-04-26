-- Education progress table
-- Stores per-user study progress for landing-page PDFs, flashcards, and quizzes.

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS education_progress (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    item_type VARCHAR(20) NOT NULL CHECK (item_type IN ('pdf', 'flashcard', 'quiz')),
    item_key VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started', 'in_progress', 'completed')),
    percent_complete INTEGER NOT NULL DEFAULT 0 CHECK (percent_complete >= 0 AND percent_complete <= 100),
    progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    completed_at TIMESTAMP,
    last_interacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, item_type, item_key)
);

CREATE INDEX IF NOT EXISTS idx_education_progress_user ON education_progress(user_id);
CREATE INDEX IF NOT EXISTS idx_education_progress_user_status ON education_progress(user_id, status);
CREATE INDEX IF NOT EXISTS idx_education_progress_updated ON education_progress(updated_at DESC);

DROP TRIGGER IF EXISTS update_education_progress_updated_at ON education_progress;
CREATE TRIGGER update_education_progress_updated_at BEFORE UPDATE ON education_progress
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
