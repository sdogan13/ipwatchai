-- Add refund columns to payments table (idempotent)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'refund_status'
    ) THEN
        ALTER TABLE payments ADD COLUMN refund_status VARCHAR(30);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'refund_amount'
    ) THEN
        ALTER TABLE payments ADD COLUMN refund_amount DECIMAL(10,2);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'refunded_at'
    ) THEN
        ALTER TABLE payments ADD COLUMN refunded_at TIMESTAMP;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'refund_reason'
    ) THEN
        ALTER TABLE payments ADD COLUMN refund_reason TEXT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'payments' AND column_name = 'iyzico_refund_response'
    ) THEN
        ALTER TABLE payments ADD COLUMN iyzico_refund_response JSONB;
    END IF;
END $$;
