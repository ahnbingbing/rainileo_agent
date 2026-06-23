# Session handoff — 2026-06-24 (dawn)

Long session. Started from the stalled "Giri rubber-stamp" thread, built deterministic
gates, fixed/regenerated the whole 6/24 batch, and did a deep iteration on the AV
themed-overlay capability. PD went to sleep after the churu jackpot landed.

## 6/24 publish board — ALL 4 scheduled (live-verified, private + publishAt)
| Slot (KST) | Episode | video_id |
|---|---|---|
| 08:00 RF | 막내 레오 떼쓰기 (엄마/랴니엄마 따라 나가려) — today's clip 0EE17D60 | `hycVba2gwjg` |
| 12:30 AV | 꿈나라 — animated dream thought-bubble (shrimp) + cut3 caption fixed | `b6DA1l6jLfw` |
| 18:00 RF | 랴니와 단짝 삐용이 (8년 전 아침 산책) | `42bkIoseIOk` |
| 21:00 AV | 거실 카지노 — JACKPOT marquee + Seedance ribbon-burst overlay | `v6e4LZ7XIPk` |

All four were hand-fixed/regenerated this session (the 00:00 auto-batch's originals were
vetoed for defects). **Verify they go public correctly through the day** ([[verify_youtube_state_via_api]] — check live API, DB lies).

## Shipped this session (committed)
- **Deterministic era-mix gate** (`reviewer._temporal_grounding_gate`): reads each cut's
  asset `captured_iso`+`subjects_csv`, fires on >1yr span OR Leo-kitten fast-growth
  (youngest Leo clip <6mo AND Leo-span >75d), stands down if a caption carries a time
  token → else score≤5 / 수정필요. Both lanes. Caught the real 030752 + 034500.
- **AV era-floor grounding (#1 generator fix)**: producer + photo_selector restrict the
  ai_vtuber asset pool to ≥ Leo's 6-month mark (≈2026-03-26) so AV can't pull pre-Leo /
  kitten footage. Env `AV_ERA_FLOOR` ("" disables for a narrated memory-lane batch).
- **Deterministic floor-sink guard** (`cameraman._ensure_sink_height_lock`): auto-injects
  the counter-mount height lock into any cut prompt mentioning a sink/세면대.
- **PD per-clip ground-truth → Giri** (`reviewer._pd_groundtruth_block`): pulls each cut's
  asset `pd_notes` and feeds Giri so caption-truthfulness is judged vs PD's stated content
  (the friend is 삐용이, this is the car-seat day, etc.). Recording side = `pd_correct_asset.py` / `assets.pd_notes`.
- **Imagination + theme must be delivered VISUALLY** — review-learning encoded into Writer
  (`writer_story.md`), Director (`director_shots.md`), Giri (`reviewer.py`): mark imagination
  cuts (misty + "○○의 상상 속!" label); a themed concept must show its theme on screen (renderable
  or an `overlay_fx`), never caption-only; Giri flags "테마 미표시(캡션-only)" cap≤6 and
  "상상/현실 구분 불명확".
- **`scripts/overlay_fx.py`** — codified themed-overlay compositor (the churu jackpot root-cause
  fix). One command: gen graphic ON BLACK with margins → Seedance party-popper burst →
  black-crush(curves) + clean lumakey (no boundary box) → composite at TOP (face-clear),
  aspect-preserved. `--anim` reuses an animated overlay to iterate free. Director sets the
  full `overlay_fx` spec; Cameraman runs the tool (no per-cut hand-tuning).
- All prompt edits rewritten **principle-first** (prompt-authoring) — no verbatim quotes /
  dates / war-stories in prompt bodies.

## The hard lessons (why things took long)
- **Rule TEXT doesn't fix Giri** — Giri rubber-stamped era-mix at 9/10 even after prompt
  rules. Fix = deterministic gates feeding Giri a boolean (the giri-update principle). Same
  pattern is the right move for the remaining LLM-trusted checks.
- **AV themed overlay took 12 iterations (churu jackpot)** — root cause: spec discovered one
  PD note at a time (style→size→position→motion→clean-key) + compositing traps re-hit
  (stretch-clip, screen-blend pink washout, lumakey boundary box, source-image no-margins).
  Fixed by codifying both into `overlay_fx.py` + the Director spec checklist. Lesson: lock the
  FULL visual-effect spec up front and run it through the tool.
- **AV cuts are 720×1280 native**; recaption_finish upscales to 1080×1920. Composite/blend at
  the native size (blend size-mismatch bug). `recaption_finish` workflow: edit `animated/<tag>.mp4`,
  rewrite captions.json (scene windows in NATIVE cut time), list ALL cuts in `_tempo_factors`
  (missing → 1.3× silent shrink). Run scripts with `PYTHONPATH=<repo>`.

## ⚠️ Open / next
- **★ icloud-sync VLM is failing in bulk** — the manual sync ingested 26 un-ingested "Ryani &
  Leo" album items but VLM-tagged only 3/26 (23 errors "Expecting value: line 1 col 1" = empty
  LLM response, likely rate-limit / thinking-mode). The nightly `com.rianileo.icloud-sync`
  (01:30, `icloud.sync --album "Ryani & Leo" --vlm --warm`) is therefore NOT draining the album →
  PD's "동물 영상 자동→GCS" expectation silently broken. Investigate the VLM call in
  `tag_assets_vlm.py` (thinking-off? key? retry/backoff?).
- **overlay_fx auto-wire**: the Cameraman does NOT yet auto-run `overlay_fx.py` when the Director
  sets a cut's `overlay_fx`. Currently manual. Wiring it into the cameraman render path is the
  follow-up so themed overlays are automatic.
- **Remaining LLM-trusted Giri checks → make deterministic** (same as era-mix): caption⇄clip
  match, AV bg-drift, too-static, subject under-shown. [[giri_rubberstamps_0623_batch]] lists them.
- **PD asked if review→Giri/agents is AUTOMATIC** — it is NOT; review-learning/giri-update are
  invoked manually. PD floated a settings hook ("PD review detected → run skill"). Not built.
- Already-scheduled known-bad dupes from prior batches may still exist — spot-check the channel.

## Key files / tools touched
`agents/reviewer.py` (era-mix gate, pd_groundtruth, theme/imagination checks),
`agents/producer.py` + `agents/photo_selector.py` (AV era-floor), `agents/cameraman.py`
(sink guard), `scripts/overlay_fx.py` (NEW), `scripts/recaption_finish.py` (recaption tool),
`scripts/render_av_one.py` (directives: shrimp_paradise / takbae_box / churu_twist),
`agents/prompts/{writer_story,director_shots}.md`.
Memories: [[av_imagination_and_theme_visual_delivery]], [[av_seedance_render_quality_lessons]],
[[giri_rubberstamps_0623_batch]].
