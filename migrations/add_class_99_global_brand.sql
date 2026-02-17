-- Migration: Add Class 99 (Global Brand) to nice_classes_lookup table
-- Class 99 represents a "Global Brand" that covers all 45 Nice classes
-- Run this script to add Class 99 support

-- Insert Class 99 (Global Brand) into nice_classes_lookup table
-- Only insert if it doesn't already exist
INSERT INTO nice_classes_lookup (class_number, name_tr, name_en, description_tr, description_en)
SELECT 99,
       'Global Marka (Tum Siniflar)',
       'Global Brand (All Classes)',
       'Bu ozel sinif, markanin tum 45 Nice sinifini kapsamasi gerektigini belirtir. "Global Marka" olarak da bilinir.',
       'This special class indicates that the trademark covers all 45 Nice classes. Also known as "Global Brand".'
WHERE NOT EXISTS (
    SELECT 1 FROM nice_classes_lookup WHERE class_number = 99
);

-- Verify Class 99 was added
SELECT class_number, name_tr, name_en
FROM nice_classes_lookup
WHERE class_number = 99;

-- Optional: Add a comment to the table explaining Class 99
COMMENT ON TABLE nice_classes_lookup IS
'Nice Classification lookup table. Class 99 is a special "Global Brand" designation that covers all 45 classes.';
