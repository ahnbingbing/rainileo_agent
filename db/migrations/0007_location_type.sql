-- 0007: Add location_type for home/aunt/outdoor classification
ALTER TABLE assets ADD COLUMN location_type TEXT;
-- values: 'home' (할머니집 충주), 'aunt' (이모집 판교), 'outdoor', 'unknown'
ALTER TABLE assets ADD COLUMN gps_lat REAL;
ALTER TABLE assets ADD COLUMN gps_lon REAL;
