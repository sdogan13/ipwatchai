-- Migration: Add profile fields to users and organizations tables
-- Run this script to add the new profile-related columns

-- Add profile fields to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS title VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS department VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS linkedin VARCHAR(200);
ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

-- Add organization profile fields
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS tax_id VARCHAR(50);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS industry VARCHAR(100);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS phone VARCHAR(50);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS website VARCHAR(200);
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS email_notifications BOOLEAN DEFAULT TRUE;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS weekly_report BOOLEAN DEFAULT TRUE;

-- Create index on users.updated_at for efficient queries
CREATE INDEX IF NOT EXISTS idx_users_updated_at ON users(updated_at);

-- Verify the changes
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'users'
AND column_name IN ('title', 'department', 'linkedin', 'updated_at')
ORDER BY column_name;

SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'organizations'
AND column_name IN ('tax_id', 'industry', 'address', 'phone', 'website', 'email_notifications', 'weekly_report')
ORDER BY column_name;
