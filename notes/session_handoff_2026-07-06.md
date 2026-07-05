# Session handoff — 2026-07-06 (07-06 batch rebuild + RF caption grounding, Layer 1→2→3 + ingest pipe)

Marathon session. The spine: PD reviewed the auto-made 07-06 batch and it was wrong in
many ways (grotesque AV wink, ungrounded RF captions), so we **rebuilt all 4 slots by
hand AND fixed the roots durably** so the pipeline stops making those mistakes. Every
durable change is committed + deployed (push = deploy; VM at HEAD `d16db18`).

## VM is authoritative and runs 24/7, independent of this Mac.
Deploy = `git push origin main` → deploy timer (2min) pulls → smoke → restart bot. Verify
by checking the VM HEAD, not just the push.

## 1. 07-06 batch — all 4 slots rebuilt + scheduled on YouTube (public-ready)
| slot (KST) | video_id | content |
|---|---|---|
| 08:00 | `5vjwNy1HnDk` | RF — Ryani scent-patrol walk (captions anchored to each beat: sniff→walk→sit=mark) |
| 12:30 | `S75-KBvUafU` | AV — 에어컨, wink cut re-rendered (neck fixed) |
| 18:00 | `YM2mx9Lsb8k` | RF — night-cafe Leo mukbang + Ryani's envious inner voice |
| 21:00 | `7p2WIHmXOdQ` | AV — 사본 (natural wink) |
Local copies: `data/output/final_0706/`.

**Root confusion that cost time:** local Mac renders vs the VM's YouTube schedule were
disconnected (the batch's cards live in the VM DB; my Mac renders were separate). Fix
going forward: Mac renders live in GCS only; **replace/schedule YouTube from the Mac's
force-ssl token** (`youtube.upload.upload_short` / `videos().delete`) or VM `reupload_episode`.

## 2. RF caption grounding — the recurring root, fixed in layers (all shipped)
PD kept catching captions that didn't match the footage: 무릎 vs chest, 한밤중 vs just
night, 물그릇 vs dishwashing, "마킹" over a WALKING beat, 11 captions crammed into a 17s
clip's last 0.5s. Root = captions were written from thin upstream tags, never from the
clip's actual per-moment action. Layered fix:
- **L1 — action/object grounding** (`_rf_caption_grounding_gate`, 81fc5e1): the VLM now
  reports `claimed`/`claimed_visible` — a caption's asserted object/body-part/action must
  be ON SCREEN, else re-ground to the real action.
- **L3 — opening visibility** (81fc5e1): the hook must open on our pet (per-window, not
  per-cut) — the walk clip that only showed Ryani at cut2 is flagged + re-grounded.
- **caption-count cap** (b2f9180): more captions than clip_dur/min_read → drop the
  trailing excess (no crammed unreadable ending).
- **over-specification + motion** (b886f61): flag `overspecified` (exact time/position the
  frames can't confirm); the rewrite preserves real motion (don't flatten walking into
  "sitting/focused").
- **여운 tail** (c4dd0ca): CAPTION_TAIL_SEC 0.8→1.5 so the last caption is readable.
- **★ L2 — action-grounded captions, UPSTREAM** (`_rf_action_grounded_captions`, 0796999):
  the real fix PD demanded. For each RF cut it samples 3–6 frames, the VLM reads the
  ACTION ARC and splits it into beats (sniff→walk→squat=mark), and writes one grounded
  caption per beat at that beat's window — naming our pets, Ryani's inner voice where it
  fits, no over-specification, and a marking line ONLY on the squat/sit beat (a dog marks
  when it STOPS, not while walking). Runs first in the RF flow; L1/L3 + cap + tail are the
  safety net. Fail-safe (`RF_ACTION_CAPTIONS=0`). Verified on a sniff→walk→mark clip.

**Lesson (canon):** RF captions MUST be read off the video's real moments. A squat/sit at
a pole/curb = marking. Don't over-specify beyond the frame.

## 3. Other durable ships this session
- **AV wink anatomy guard** (339623b): the grotesque neck came from forcing a
  front-facing face onto a belly-up body — `_build_wink_cut` + `_av_still_compose_prompt`
  now keep head+body one orientation, neck never twisted (lying poses are fine).
- **Stray-animal [EXCLUDE]** (027a33a): `_branding_asset_ids` drops pd_notes `[EXCLUDE]`
  too; `scripts/mark_pool_exclude.py` tags by asset_id. Marked the 2020-12-23 street-cat
  cluster (Mac+VM DB). Not-our-pet footage never enters the pool.
- **Legacy producer proposal removed** (b0f99a4): the 18:00 "영상 제안 (2편)" propose→wait
  cron is gone — launch (03:00) is the automatic pipeline.
- **YouTube comment scope** (eaced4b): commentThreads needs `youtube.force-ssl` (not
  readonly). Token grant must precede the code that requests it (re-consented, SM v2, VM
  re-fetch, then deploy). Child-face privacy: YuNet face-blur (`scripts/_blur_faces.py`).
- **VM resized** to e2-standard-2 (dedicated cores) — the e2-medium wedged under a batch,
  starving sshd. **A heavy parallel batch can make the box SSH-unreachable; a `reset` is
  the reliable non-SSH kill.**
- **★ ingest_register** (d16db18): closes the icloud→VM DB gap. Mac sync mirrors FILES to
  GCS; `scripts/ingest_register.py` carries the ROWS — Mac `--export` (wired into
  `icloud_full_sync_chunked.sh`) snapshots the assets table to
  `gs://rianileo-assets/db_sync/assets.jsonl`; VM `--import` (crontab, every 30min) upserts
  by asset_id (idempotent). Verified: VM gained 3192 new asset rows. New footage now
  reaches the VM Writer pool.

## What runs overnight (PD asleep, Mac closed)
**VM — 24/7, autonomous (does NOT need this Mac):**
- **03:00 KST cron → `launch_selfheal`** makes the 07-07 batch **with Layer 2 live** —
  auto-renders, Giri-gates, schedules passing slots public, empties failures (no junk).
- **`ingest_register --import`** every 30min — keeps the VM DB fed with new assets.
- Slack **board bot** 24/7; bandit / macro-cache / board-escalation crons.

**Mac — STOPS when the Mac sleeps:**
- The iCloud **backlog drain** (`icloud_full_sync_chunked.sh`, ~8155 items left) is a Mac
  `nohup` job — it PAUSES on sleep, resumes on wake (or re-run it). Its final step runs
  `ingest_register --export`, so once it finishes the VM picks up the new rows automatically.
- This Claude CLI session ends when you leave.

## ★ NEXT (when back)
1. **Spot-check the 07-07 batch** (03:00 cron output) — this is **Layer 2's first
   production run**. Confirm the RF captions match each on-screen action beat (the whole
   point). `/veto` any slot that's off.
2. **Finish the iCloud backlog drain** if the Mac slept — re-run
   `bash scripts/icloud_full_sync_chunked.sh` (it auto-exports at the end → VM auto-imports).
3. **Burst-photo → video** idea (PD's, not built): same-location close-timestamp photo
   clusters → fast photo-sequence OR Seedance interp fill. Design pending.
4. **merge-retrospective sync** — this session shipped a lot; fold the lessons
   (RF grounding layers, wink anatomy, Mac/VM disconnect, ingest pipe) into
   `notes/retrospective_2026-05_to_07.md` (started D18/D19; the rest is unrecorded).
