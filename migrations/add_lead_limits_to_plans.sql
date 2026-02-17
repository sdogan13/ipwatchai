-- Add lead access limits to subscription plans
ALTER TABLE subscription_plans
ADD COLUMN IF NOT EXISTS daily_lead_limit INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS can_access_leads BOOLEAN DEFAULT FALSE;

-- Update plan limits
UPDATE subscription_plans SET daily_lead_limit = 0, can_access_leads = FALSE WHERE name = 'free';
UPDATE subscription_plans SET daily_lead_limit = 0, can_access_leads = FALSE WHERE name = 'starter';
UPDATE subscription_plans SET daily_lead_limit = 5, can_access_leads = TRUE WHERE name = 'professional';
UPDATE subscription_plans SET daily_lead_limit = -1, can_access_leads = TRUE WHERE name = 'enterprise';  -- -1 = unlimited

-- Add daily usage tracking to api_usage
ALTER TABLE api_usage
ADD COLUMN IF NOT EXISTS leads_viewed INTEGER DEFAULT 0;
