# Session Handoff — 2026-06-21 (dawn)

Long remote-control session (PD steering live). Theme: fix the launch download
failures, re-render/repair the 6/21 batch, ship corrected AV+RF, harden + log the
launch batch. Everything below is **committed to working tree but NOT git-committed** —
review + commit when ready.

## ★ TOP PRIORITY next session — verify tonight's 03:00 launch batch
The PD's main ask: **see the 03:00 batch succeed.** When you start, check, in order:
1. Slack launch threads — per-slot `예약완료` (success) vs `슬롯 비움` (empty).
2. `data/logs/launch.out.log` → `런칭 완료 — 렌더 X/4, 예약업로드 Y편`.
3. **`data/logs/batch_problems.jsonl`** (NEW) — any prefetch failure now records the REAL
   cause: `"reason"` = `osxphotos SLOW WINDOW (transient)` vs `asset unavailable`, plus
   `osxphotos_healthy`, failed asset ids, budget, slot/concept.
4. If a slot failed on a **SLOW WINDOW** (transient 3–6am osxphotos slowness): just
   **re-fill it in the morning** when healthy. Health check:
   `.venv/bin/python -c "from icloud.sync import _osxphotos_healthy; print(_osxphotos_healthy())"`
   Re-render one card: `render_card(card_id, use_brain=False)` (RF) or
   `scripts/render_av_one.py {hawaii|homecam|chimipja}` (AV).

A persistent monitor on `launch.out.log` was watching this session — it dies on session
end, so re-check the logs manually next session.

## Scheduled for 6/21 (LIVE-VERIFIED on YouTube, all 예약됨)
| KST | concept | video_id | card |
|---|---|---|---|
| 08:00 | 침입자 (caption-fixed: 벽→"아무것도 없잖아", 바람→불독엄마 다독임) | EloAhUTmfUk | ab168548 |
| 18:00 | 하와이 드림 (multi-space return-house fixed) | Gr3St9V0VGA | 38468b6e |
| 21:00 | 크리스마스 어색한 랴니 (PD concept, captions scaled to fit) | ceuD9BER7MM | 125b9757 |

Always verify schedule via the **YouTube API**, not the DB (DB youtube fields go stale —
the DB claimed 6/21 was full when the live channel was empty). 12:30 RF slot left open.

## Fixes landed tonight (uncommitted)
**Download / launch resilience**
- `icloud/sync.py`: `prune_originals` is disk-pressure-aware (skip when free ≥
  `ICLOUD_PRUNE_FREE_FLOOR_GB`=30; mtime keep-window) — root cause of the wipeouts (prune
  was deleting ~the whole cache so every render depended on a fragile re-download).
  `warm_working_set()` + `--warm` flag (wired into launchd icloud-sync.plist + repo plist):
  bounded local cache, stops above the prune floor. `_osxphotos_healthy()` probe.
  `download_assets_by_uuids`: per-attempt cap (`PREFETCH_ATTEMPT_S`=200) + `max_attempts`
  6 + slow-window backoff → actually waits out a transient window.
- `agents/cameraman.py`: `PREFETCH_BUDGET` 600→1200; `_log_batch_problem()` →
  `batch_problems.jsonl` + 🚨 Slack with health-probe diagnosis.
- `agents/cameraman.py`: **`_video_first` → `_rf_has_video` NameError** fix — crashed any
  RF concept that kept a photo cut (first RF was all-video so it slipped by).

**AV quality (Director / canon / still-gen)**
- `agents/writer_director.py`: heavy Writer/Director calls now **Anthropic-primary**
  (OpenAI gpt-4.1 can't emit a 16k-token JSON in the 45s fail-fast timeout — PD's hint);
  Director dict-shape **unwrap** (Anthropic returns wrapper/single-concept dicts);
  `_parse_json_loose` strict=False (control-char tolerance). `producer.py` legacy parse strict=False.
- `agents/prompts/director_shots.md`: camera energy matches the beat (static for
  observational/home-cam, movement+dual-motion for action — fixes the 침입자 static feel);
  reference-IMAGE age rule (present-day = `ryani_solo` adult, `ryani_young` only for
  flashbacks); wink = natural anatomy (no head-turn → no "zombie" 180° neck twist).
- `agents/canon.py`: Leo/Ryani **relative size** grounding (Leo = small young cat, never
  bigger than the stocky adult Frenchie).
- `agents/caption_salvage.py`: species anchor in the VLM *describe* step (fixes
  "랴니를 레오라고" caption name-swap).
- `scripts/generate_character_scene.py`: **space-aware still anchor** — a cut returning to
  a space shown earlier locks to that space's first frame (fixes Hawaii return-house = a
  different home, and single-space background drift).

**New tools**
- `scripts/render_av_one.py {hawaii|homecam|chimipja}` — produce+render one AV from a
  prescriptive PD directive (CONCEPT_BRAINSTORM=0), bypassing launch slots.
- `scripts/cctv_finish.py` — home-cam CCTV grade (barrel curvature + grain + REC HUD;
  text drawn flat over the bowed footage).
- `scripts/recaption_finish.py` — re-caption an existing render with NO re-render ($0);
  **scales to 1080×1920 before drawtext** (else fonts overflow on 720 Seedance / real footage).

## Open follow-ups (priority)
1. **★ White spot on Ryani's neck/back** — recurring Seedance i2v drift. Text canon can't
   catch it (refs are frontal, back unconstrained). Needs an IMAGE-level fix: back-aware
   still-selection or a back-inclusive reference. PD raised it twice; deliberately NOT
   hacked at 1am (rule-base hacks have backfired). Do this first.
2. **Disk / GCS** — free disk sits at the 30GB prune floor → warm cache capped ~7GB → the
   batch still on-demand-downloads old memory-lane clips (the 3am failure surface). GCS
   migration would let the warm cache go deep and largely end this class of failure.
3. **RF freshness** — cooldown windows too shallow; reused the 2018-07-04 한강 outing across
   6/12–6/18. Used env overrides tonight (`RF_COOLDOWN_RECENT_CARDS=80
   RF_CLIP_COOLDOWN_EPISODES=12`); make durable after it's validated (RF tuning has
   net-degraded before — change carefully).
4. **침입자 wink** still lands on Leo, not Ryani (Seedance favors the cat). Minor; shipped
   caption-only. Pursue only if PD wants Ryani's wink specifically.
5. **CCTV grade** integration into the home-cam burn stage (pending PD OK on strength).

## PD feedback / lessons this session
- Verify YouTube via the API, not the DB ([[verify_youtube_state_via_api]]).
- Prompt-authoring: principle-first, NOT rule-base ban-lists (got chided; reverted the
  ⛔ blocks). Use the prompt-authoring skill.
- Render cost: AV ≈ $50 — validate cheaply (1 still ≈ $0.04) before paid renders.
- `recaption_finish` / any external caption burn must scale to 1080 first.
