-- Grandfather existing users: mark all users created before 2026-02-18 as email-verified.
-- These users registered before verification was required and should not be blocked.
-- Run once after deploying the email verification feature.

UPDATE users
SET is_email_verified = TRUE,
    email_verified_at = NOW()
WHERE created_at < '2026-02-18'
  AND (is_email_verified = FALSE OR is_email_verified IS NULL);
