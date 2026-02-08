-- Migration: Add is_superadmin column to users table
-- Date: 2026-02-08
-- Description: System-level superadmin flag, independent of org-level roles.
--              A superadmin can access ALL organizations regardless of membership.

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN NOT NULL DEFAULT FALSE;

-- Index for quick lookup (few superadmins expected)
CREATE INDEX IF NOT EXISTS idx_users_is_superadmin ON users (is_superadmin) WHERE is_superadmin = TRUE;
