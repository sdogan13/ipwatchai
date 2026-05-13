-- Regional Stripe + iyzico payment metadata.
-- Safe to run after migrations/payments.sql and migrations/credit_packs.sql.

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS provider VARCHAR(32) NOT NULL DEFAULT 'iyzico',
    ADD COLUMN IF NOT EXISTS region VARCHAR(8),
    ADD COLUMN IF NOT EXISTS billing_country VARCHAR(128),
    ADD COLUMN IF NOT EXISTS stripe_checkout_session_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS stripe_payment_intent_id VARCHAR(255),
    ADD COLUMN IF NOT EXISTS stripe_raw_response JSONB;

UPDATE payments
SET provider = 'iyzico'
WHERE provider IS NULL;

CREATE INDEX IF NOT EXISTS idx_payments_provider
    ON payments(provider);

CREATE INDEX IF NOT EXISTS idx_payments_provider_status
    ON payments(provider, status);

CREATE INDEX IF NOT EXISTS idx_payments_region
    ON payments(region);

CREATE INDEX IF NOT EXISTS idx_payments_stripe_checkout_session_id
    ON payments(stripe_checkout_session_id)
    WHERE stripe_checkout_session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_stripe_subscription_id
    ON payments(stripe_subscription_id)
    WHERE stripe_subscription_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_stripe_payment_intent_id
    ON payments(stripe_payment_intent_id)
    WHERE stripe_payment_intent_id IS NOT NULL;
