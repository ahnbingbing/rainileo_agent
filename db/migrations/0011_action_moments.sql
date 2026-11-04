-- 0011: Add action_moments — the per-clip TEMPORAL ACTION timeline from the Gemini
-- native-video layer (scripts/video_actions.py). Frame-sampled VLM tagging (scene_description,
-- activity) samples 6-10 stills and is blind to a 1-2s action BETWEEN frames ("Leo walks out
-- the door", "Leo climbs on Ryani's back") — so clip SELECTION can't find a clip by what actually
-- happens and CAPTIONS get mis-attributed. action_moments stores a JSON list
-- [{start,end,actor,action,desc}] watched from the whole clip. Consumed by agents/asset_embed.py
-- (search doc) and grounded natively at caption time in agents/cameraman.py. Populated by
-- scripts/backfill_actions.py. (agents.clip_actions.ensure_column also adds this idempotently
-- at runtime; gated for consumption by env USE_CLIP_ACTIONS.)
ALTER TABLE assets ADD COLUMN action_moments TEXT;
