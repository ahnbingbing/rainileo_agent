# Session handoff — 2026-06-22 (evening)

> Cold-start reference for the next CLI. Read CLAUDE.md first, then this.
> Branch: `approach-d-grounded-singlepass`. Everything below is **committed**
> (5 commits `8cc8802`→`f2c5cca`) unless marked otherwise.

## ★ NEXT (do first)

1. **Veto / regenerate the two already-scheduled 6/23 episodes that have the bugs we
   fixed today** — the fixes prevent RECURRENCE but do NOT un-schedule these:
   - `fXIY_mc83p0` — 카페 간식대전: **duplicate** of an earlier 2025-11-21 cafe outing.
   - `qk2l8T6Btxo` — 흙길 5년전: a **2020 stray cat mislabeled "레오"** (Leo adopted 2025-10).
   Verify via live YouTube API (DB is stale — memory `verify_youtube_state_via_api`), then
   `/veto` + re-make. PD asked; not yet done.
2. **Re-VLM pre-2025 footage** to finish cleaning bug-2 residue. The subject DATA is fixed
   (624 assets stripped of false 'leo' + a live guard), but old clips' `scene_description`
   PROSE still says "오렌지 고양이 레오" and some cats are still mislabeled as the DOG Ryani.
   A re-tag pass with today's improved VLM prompt fixes both. Cheap now (VLM is ~60/min
   after today's speedup). e.g. `tag_assets_vlm.py --since '2025-09-25' --kind photo` style,
   or a targeted `captured_iso < '2025-09-25'` re-run.
3. **Activate the escalation picker** if PD wants the board bot's "CLI가 처리" to be real
   (built + verified today, NOT activated — see below). PD's call.

## What shipped this session (committed)

| commit | what |
|--------|------|
| `8cc8802` | **backlog perf**: VLM 6-worker parallel + `thinking_budget=0` + retry/backoff (`tag_assets_vlm.py`); `ICLOUD_SKIP_PHASH` for the chunked backlog (`icloud/sync.py`, `petlabels_chunked.sh`). |
| `da94e24` | **slack+render**: message-event dedup + ack-fast (board/grandma LLM off-thread); `burn_captions` no longer infinite-retries on absent-upstream cuts. |
| `8cc9cfb` | **board honesty**: `_act_status` lists open CLI escalations; escalate reply no longer over-promises. |
| `9242d76` | **escalation picker** (built; NOT activated). |
| `f2c5cca` | **RF correctness**: primary-outing cooldown (re-run fix) + temporal subject grounding (Leo pre-adoption). |

### pet-label backlog — DONE ✅
Drained in 7 rounds. **16,478 / 16,502 tagged** (NA 24 = errors/unsupported). Two real
bottlenecks found & fixed (neither was the network — PD's instinct "뭔가 잡아먹는다"):
- **VLM tagger** was sequential + the only repo VLM caller leaving thinking ON. → 6 parallel
  workers (DB writes stay main-thread, sqlite-safe) + `thinking_budget=0`. ~33s→~1s/photo.
- **phash** software-decoded the FULL HEIC per photo (libheif = the 208% CPU hog) just for a
  64×64 hash. → `ICLOUD_SKIP_PHASH=1` in the backlog only (daily pipeline still computes it;
  phash=None already valid). Round wall-time ~70min(never finishing)→~16min.
- The launchd `com.rianileo.petlabel-backlog` (07:00) is now idempotent-done; it'll re-run
  tomorrow, find "nothing new", and just maintain coverage for newly-added photos.
- Progress log: `data/logs/petlabel_backlog_progress.md`.

### Mac / power
- **Rebooted** → container free 2.7GB → **85GB** (the stuck OS-update snapshot released).
- **Sleep disabled permanently**: PD ran `sudo pmset -a sleep 0 disksleep 0 powernap 0`
  (verified battery+AC). caffeinate no longer needed.

### Slack bot — was wedged, now healthy
The `com.rianileo.slack` listener (Socket Mode, `slack/app.py`) had been stuck ~22h in a
`BrokenPipeError` reconnect loop (73k+ errors) AND an inline render retry-loop. Restarted
(now PID under `com.rianileo.slack`, clean, 0 BrokenPipe). Root fixes so it doesn't recur:
- **Duplicate/replayed messages** (PD saw the bot re-answer old messages): the message
  handler ran a slow inline LLM (board_agent/grandma) → missed Slack's 3s ack → Slack
  retried the event → handled twice; a restart also replays the unacked backlog. Fixed:
  heavy work off-thread (ack fast) + `_already_processed()` DB dedup on client_msg_id.
- **burn_captions infinite-retry**: a Director cut with no veo_prompt is skipped upstream →
  its mp4 is absent → burn counted that as a failure → rc=1 → retry_loop re-rendered the
  same deterministic miss forever. Fixed: absent-upstream = skip (not fail); succeed if
  ≥1 cut produced & no real ffmpeg error; still fail if produced==0.

### Board escalation picker (built, NOT activated)
`scripts/process_board_escalations.py` + `launchd/com.rianileo.board-escalations.plist`.
The board bot queued repo-level requests to `board_escalations` and promised "CLI가
처리" — but nothing consumed the queue (dead-letter; 3 identical unhandled rows piled up).
The picker (every 20 min) runs a HEADLESS Claude Code **read-only** (`--allowedTools
Read,Grep,Glob --output-format json` → cannot edit/commit/Bash), posts the analysis to
`rayleo_board`, marks handled. End-to-end verified. **Not auto-activated** — a persistent
autonomous agent that auto-posts to shared Slack is PD's opt-in:
```
cp launchd/com.rianileo.board-escalations.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rianileo.board-escalations.plist
```
Kill switch: `BOARD_PICKER_ENABLED=0` or `launchctl unload`.

### RF correctness (the two 6/23 bugs PD caught)
1. **Same-outing re-run** (cafe dup): cooldown was reverted to exact-asset_id on 6/17, so a
   2nd episode reusing the same cafe outing via DIFFERENT files passed; the reviewer
   backstop only compares PUBLISHED videos. Fix: `_recently_used_rf_primary_sessions()`
   cools the capture-date of any outing a produced card used as PRIMARY (≥2 cuts);
   `_rf_is_cooled` rejects those — re-run blocked without the 6/17 over-coarseness; ≥6
   relax still protects the Writer. (`agents/producer.py`)
2. **Leo in pre-adoption footage** ("5년 전 레오" on a 2020 stray): Leo's existence was prose-
   only / VLM got no date. Fix (defense-in-depth): `canon.LEO/RYANI.exists_from` +
   `canon.pet_exists_on()` (single source); `producer._ground_subjects()` strips impossible
   pets from RF candidate `sub`; `tag_assets_vlm` injects capture date + "Leo didn't exist
   before 2025-09 / Ryani is a DOG" into the VLM prompt; **one-shot DB fix stripped false
   'leo' from 624 pre-adoption assets**. Residual = old prose + cat-as-Ryani species (item 2
   above).

## Still open (from prior handoffs, not addressed)
- RF freshness tuning (broader than the outing fix).

### DONE 2026-06-22 (later) — Ryani markings + 삐용이
- **흰점(목뒤) fix**: PD clarified 목**앞**(throat)=흰색 OK / 목**뒤**(nape)=순검정. Superseded the
  coarse "neck SOLID BLACK" canon (which both let the nape-dot slip and risked suppressing the
  legit front-throat white) across canon.py (RYANI_MARKING/RYANI_IMAGE_CANON/REVIEW_RYANI),
  cameraman.py (preserve + _append_character_canon), generate_ref_sheets.py, character_sheets.md.
  Reviewer now flags a nape/back-of-neck white spot but NOT the front-throat white. Same pass
  fixed a stale ref_sheets eye-dot drift. (memory `ryani_nape_no_white`)
- **삐용이 promoted**: into canon.CHARACTER_FACTS friend-cast (alongside 태풍/남산이) + RF prompts
  (realfootage_concept.md, writer_realfootage.md) — tail/tuxedo disambiguation (꼬리 달린 흑백 개 =
  삐용이 ≠ 꼬리없는 랴니) + 추모-민감 tone guard. (memory `bbiyong_canon`)

## Notes
- Verify YouTube state via **live API**, never the DB (memory `verify_youtube_state_via_api`).
- A full AV render ≈ $50 — validate cheaply, get PD $-OK before paid renders.
