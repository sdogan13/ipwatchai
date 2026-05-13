-- Rename feature flag: feature.opposition_radar_enabled -> feature.radar_enabled
-- Idempotent: drops any pre-existing new-key row before renaming.
DELETE FROM app_settings
WHERE key = 'feature.radar_enabled'
  AND EXISTS (SELECT 1 FROM app_settings WHERE key = 'feature.opposition_radar_enabled');

UPDATE app_settings
SET key = 'feature.radar_enabled',
    description = 'Enable Radar leads',
    updated_at = NOW()
WHERE key = 'feature.opposition_radar_enabled';
