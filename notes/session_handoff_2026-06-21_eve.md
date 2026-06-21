# Session handoff — 2026-06-21 (evening)

> Cold-start reference for the next CLI. Read CLAUDE.md first, then this.
> Branch: `approach-d-grounded-singlepass`. Everything below is **committed**.

## ★ NEXT (do first)

1. **REBOOT the Mac.** Disk is critically low (**2.7GB container free**) and stuck —
   `tmutil deletelocalsnapshots /` fails with "stale NFS file handle". Root cause:
   a **prepared macOS update** is waiting to install (`com.apple.os.update-MSUPrepareUpdate`
   snapshot holds space) + **35-day uptime** (accumulated stale handles / unpurged
   purgeable). A reboot installs the update, releases the snapshot, reclaims purgeable,
   and lets Photos "Optimize Mac Storage" (already ON) offload originals. Expect several
   GB back. After reboot, check `diskutil info / | grep "Free Space"`.
2. **pet-label backlog** is PAUSED, will auto-resume. The launchd job
   `com.rianileo.petlabel-backlog` (07:00 daily) resumes it; the script self-stops in the
   01:00–06:59 protected window and has a 3GB disk guard, so it only runs when there's room.
   After reboot frees disk, it resumes on its own (or run `BATCH_GB=3 bash scripts/petlabels_chunked.sh`
   manually outside 01:00–07:00). Progress: VLM-tagged **5,814 / 6,451**; whole-library
   backlog **~10k un-ingested** (1.5–2 days to drain).
3. **Tonight's 03:00 launch batch is the FIRST live use of Channel Manager packaging.**
   Check tomorrow AM that the AV uploads got hook titles + concept-specific tags (not
   "Ryani & Leo"). Verify via the live YouTube API, not the DB.

## PD is handling (don't touch)

- **6/22 12:30 AV slot has a duplicate + the drifted "홈캠 증거인멸"** (cut3 room-teleport
  episode) scheduled. PD said they'll veto/clean it up themselves.

## What shipped this session (committed)

Commits: `5d5f885` (prior WIP) → `4dd7678` (CM P1+2 + bgm + recaption) → `e0fa0f6`
(CM P3+4) → `46a813e` (backlog auto-resume).

### 6/22 RF episodes — made + scheduled on YouTube (live-verified)
- **08:00** `풀 뜯어먹는 강아지 봤나요? 🌿 막대기 사랑꾼 랴니 (ft. 단짝 삐용이)` →
  youtube `RWWsSUZZHFE` (private+publishAt 2026-06-21T23:00:00Z). File:
  `data/output/episodes/episode_rf_0622_grasswalk_bbiyong.mp4` (mirrored to GCS, local reclaimed).
- **18:00** `여름 정원 순찰 나선 고양이 레오 🐈 꼬리는 살랑살랑 (탐정 모드)` →
  youtube `sVQuUaY_PF0` (publishAt 2026-06-22T09:00:00Z). BGM = summer-bossa.
- These were hand-made (launch RF lane, review mode), re-captioned, then uploaded directly
  (the 6/22 batch had already run, so the pins were orphaned → manual upload). Both are
  `cards` pins (55e96d8a, ccf5506c), uploaded=1.

### 삐용이 (new canon) — memory `bbiyong_canon`
랴니의 친구, **돌아가신 보스턴테리어**(흑백 턱시도 + 꼬리有 → 랴니=프렌치불독·꼬리無 와 구분).
옛 2016 클립에 랴니와 **함께** 등장. 추모 톤은 PD 확인 필수. TODO: promote into
agents/canon.py + character_sheets friend-cast + RF grounding/caption so it's not collapsed into 랴니.

### Bug fixes
- **recaption tempo shrink** (`scripts/recaption_finish.py`): re-captions lost ~23% length
  because a fresh caption manifest omitted `_tempo_factors` → assemble applied its default
  1.3x speedup. Now inherits `_tempo_factors` from the original render + native-fills missing
  cuts. (caption_salvage was NOT affected — it edits the original manifest in place.)
- **BGM category collapse** (`realfootage_concept.md`, `cameraman_brain.py`): RF offered only
  3 moods (one a dead key) so every RF used `gentle_acoustic` → 2 tracks. Expanded both to
  the full 27-mood taxonomy (the map already had them) + principle-first selection guidance.

### Channel Manager agent (NEW) — `agents/channel_manager.py`, design `notes/channel_manager_design.md`, memory `channel_manager_agent`
Runs the channel with data; complements Giri (per-episode audience gate). 4 phases, all live:
- **P1 packaging**: `make_packaging()` = hook title + SEO description + concept-specific
  hashtags, rotating 3 tone arms (`hook_search`/`hook_strong`/`search_strong`) by card_id
  hash. Wired into the single upload chokepoint `producer.py:_auto_upload_episode` (runs only
  on real uploads; covers launch/daily/pinned; records `draft.packaging_arm`; falls back to
  static draft on LLM failure). Prompt allows generic 고양이/강아지 alongside names (early-channel
  discovery, per PD). Prompt: `agents/prompts/channel_manager_packaging.md`.
- **P2 MAB loop-closure**: bandit gained a `packaging` dimension (`video_performance.packaging_arm`
  + migration, `collect` populates it, `analyze`/`report` show it, `choose_packaging`,
  `stabilized(level)` = P(best)≥0.9 & n≥8 & ≥2 real arms). `launch.day_assignments` is now
  bandit-aware — tilts the 2-2 lane mix to 3-1 toward a stabilized winner, keeping 1 loser
  slot for exploration; **no-op until data earns it** (Latin square stays the backbone).
  Env `BANDIT_STEER=0` disables.
- **P3 recommend**: `recommend()/recommend_text()` = bandit posteriors + video_performance +
  LIVE YouTube schedule → "어디 더 넣을지"; rides on the weekly bandit Slack report. CLI:
  `python -m agents.channel_manager --recommend`.
- **P4 portfolio feedback**: `portfolio_signal()` (winning lane/timeslot/packaging pattern +
  >5% retention themes) injected into `concept_brainstorm` next to the freshness/exclude
  block — patterns only; topics still governed by freshness.

### Ops
- **backlog auto-resume**: `petlabels_chunked.sh` self-stops in the 01:00–06:59 protected
  window (01:30 sync / 03:00 batch / 3–6am Photos-maint download-failure window — the
  osxphotos lock only WAITS then proceeds WITHOUT exclusivity, so overlap re-creates the
  6/19 PhotoKit contention). launchd `com.rianileo.petlabel-backlog` @07:00 (installed+loaded).
- **output reclaim**: `data/output` (4.47GB) mirrored to GCS + local cleared (on YouTube+GCS).

## Disk situation (the rabbit hole — context for next session)

228GB disk, APFS container shared across volumes → `/` looks small but **container free is the
real number (2.7GB)**. Bulk is `/Users/ligi` (pipeline's Photos source — DON'T reduce it) +
the 35GB `/Users/ahnbingbing/Pictures` library (Optimize ON, will offload after reboot).
Cache clears (Chrome `~/Library/Caches/Google`, Notion Cache, brew) freed ~2.9G but the
**OS-update snapshots hold the freed files** so container free didn't move → **reboot is the fix.**
Don't manually `rm` inside Photos libraries (corruption) or delete-in-Photos-app (deletes from
iCloud). `data/assets` (6.9G) IS safely prunable to GCS via `icloud.sync` prune — but only
AFTER the snapshots release, else deletes are held for zero gain.
Note: a name-only mass-delete of `data/assets` was correctly blocked by the safety classifier;
use the pipeline's own GCS-backed prune, not ad-hoc rm.

## Still open (from prior handoffs, not addressed this session)

- ★ 흰점(목/등) image-fix for Ryani markings.
- RF freshness tuning.
- 삐용이 canon promotion into canon.py/character_sheets (see above).

## Verified-live YouTube state (6/21 eve)

Scheduled (private+publishAt): 6/21 21:00 크리스마스 / 6/22 08:00 풀먹방+삐용이(RF) /
6/22 12:30 ×2 (홈캠증거인멸 + 배고픔여유 — DUPLICATE, PD cleaning) / 6/22 18:00 정원레오(RF).
Always verify via the live API, not the DB (DB can be stale — see memory `verify_youtube_state_via_api`).
