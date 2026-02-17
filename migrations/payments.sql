-- Payments table for iyzico checkout flow
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    user_id UUID NOT NULL REFERENCES users(id),
    plan_name VARCHAR(50) NOT NULL,
    billing_period VARCHAR(20) NOT NULL,  -- 'monthly' / 'annual'
    amount DECIMAL(10,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'TRY',
    iyzico_token VARCHAR(255),
    iyzico_payment_id VARCHAR(100),
    iyzico_conversation_id VARCHAR(100),
    iyzico_raw_response JSONB,
    status VARCHAR(30) DEFAULT 'pending',  -- pending / completed / failed
    paid_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_payments_org ON payments(organization_id);
CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_token ON payments(iyzico_token);
CREATE INDEX IF NOT EXISTS idx_payments_conversation ON payments(iyzico_conversation_id);

-- Add subscription date columns to organizations if missing
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'organizations' AND column_name = 'subscription_start_date'
    ) THEN
        ALTER TABLE organizations ADD COLUMN subscription_start_date TIMESTAMP;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'organizations' AND column_name = 'subscription_end_date'
    ) THEN
        ALTER TABLE organizations ADD COLUMN subscription_end_date TIMESTAMP;
    END IF;
END $$;
