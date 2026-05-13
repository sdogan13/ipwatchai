-- Credit Packs: extend payments table to support one-shot AI credit purchases
-- in addition to plan subscriptions. Backward-compatible: existing rows get
-- kind='subscription' via the default.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'kind'
    ) THEN
        ALTER TABLE payments ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'subscription';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'pack_id'
    ) THEN
        ALTER TABLE payments ADD COLUMN pack_id VARCHAR(20);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'credits_amount'
    ) THEN
        ALTER TABLE payments ADD COLUMN credits_amount INTEGER;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'discount_code'
    ) THEN
        ALTER TABLE payments ADD COLUMN discount_code VARCHAR(50);
    END IF;
END $$;

-- Make plan_name / billing_period nullable for credit-pack rows (they only
-- apply to subscription purchases). Existing subscription rows are unaffected.
ALTER TABLE payments ALTER COLUMN plan_name DROP NOT NULL;
ALTER TABLE payments ALTER COLUMN billing_period DROP NOT NULL;

CREATE INDEX IF NOT EXISTS idx_payments_kind ON payments(kind);
