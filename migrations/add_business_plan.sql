-- Migration: Add Professional tier + unified AI credits + application limits + rename old Professional to Business
-- Date: 2026-02-17

-- 1. Add AI credit columns to organizations (unified pool replacing separate logo/name credits)
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS ai_credits_monthly INTEGER DEFAULT 0;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS ai_credits_purchased INTEGER DEFAULT 0;
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS ai_credits_reset_at TIMESTAMP;

-- 2. Rename existing 'professional' plan to 'business' (the 1299₺ tier)
UPDATE subscription_plans SET name = 'business', display_name = 'Business'
WHERE name = 'professional';

-- 3. Insert new Professional plan (799₺ tier, between Starter and Business)
INSERT INTO subscription_plans (name, display_name, max_watchlist_items, max_alerts_per_month, max_reports_per_month, max_api_calls_per_day, can_use_live_search, can_export_reports, can_use_visual_search, price_monthly, daily_lead_limit, can_access_leads, name_suggestions_per_session, logo_runs_per_month)
SELECT 'professional', 'Professional', 50, 200, 20, 1000, TRUE, TRUE, TRUE, 799, 5, TRUE, 20, 20
WHERE NOT EXISTS (SELECT 1 FROM subscription_plans WHERE name = 'professional');

-- 4. Update Business plan limits (was professional, now 1299₺ tier with 10x watchlist and searches)
UPDATE subscription_plans SET
    max_watchlist_items = 1000,
    max_api_calls_per_day = 5000,
    daily_lead_limit = 10,
    name_suggestions_per_session = 30,
    logo_runs_per_month = 60
WHERE name = 'business';

-- 5. Update Enterprise plan to fully unlimited
UPDATE subscription_plans SET
    max_watchlist_items = 999999,
    max_alerts_per_month = 999999,
    max_reports_per_month = 999999,
    max_api_calls_per_day = 999999,
    can_use_live_search = TRUE,
    can_export_reports = TRUE,
    can_use_visual_search = TRUE,
    daily_lead_limit = 999999,
    can_access_leads = TRUE,
    name_suggestions_per_session = 999999,
    logo_runs_per_month = 999999
WHERE name = 'enterprise';

-- 6. Initialize AI credits for existing organizations based on their current plan
UPDATE organizations o SET
    ai_credits_monthly = CASE
        WHEN sp.name = 'starter' THEN 30
        WHEN sp.name = 'professional' THEN 100
        WHEN sp.name = 'business' THEN 300
        WHEN sp.name = 'enterprise' THEN 999999
        ELSE 0
    END,
    ai_credits_reset_at = NOW()
FROM subscription_plans sp
WHERE o.subscription_plan_id = sp.id
  AND o.ai_credits_monthly = 0;
