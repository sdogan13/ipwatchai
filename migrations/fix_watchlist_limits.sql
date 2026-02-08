-- Migration: Fix watchlist item limits in subscription_plans table
-- Date: 2026-02-08
-- Purpose: Align DB values with PLAN_FEATURES (code is primary source of truth)
--
-- Before: Free defaulted to 100 via fallback in code
-- After:  Free=5, Starter=25, Professional=50, Enterprise=500

UPDATE subscription_plans SET max_watchlist_items = 5 WHERE name = 'free';
UPDATE subscription_plans SET max_watchlist_items = 25 WHERE name = 'starter';
UPDATE subscription_plans SET max_watchlist_items = 50 WHERE name = 'professional';
UPDATE subscription_plans SET max_watchlist_items = 500 WHERE name = 'enterprise';
