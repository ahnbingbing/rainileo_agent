# Session handoff — 2026-06-26 (afternoon)

PD reviewed the 6/27 batch and had me fix all four slots + fix two systemic bugs
(Ryani harness-ring ref leak, board bot whack-a-mole). Network about to go down —
this captures the IN-FLIGHT render and exactly how to finish it.

## ⚠️ IN-FLIGHT (finish this first): 18:00 AV = Plan ABC render

The 18:00 slot currently holds the OLD Leo self-intro (`SvG-b87PxnQ`) but PD killed
that concept (a "첫인사" is awkward this late + the batch had two no-hook AVs + its
cut3+ backgrounds were mushy from ref-mode). Replacing it with **Plan ABC** (여름
에어컨 밈, a HOOK): "우리집 댕냥 Plan A·B·C — 결국 승자는 에어컨". Validator-approved 8/10.

- **Render was RUNNING** at handoff: `produce_and_render([plan_abc_concept], 2026-06-27)`,
  work dir `data/tmp/cameraman_13d4e7b1_20260626_155300/` (regen-stills phase, no
  animated cuts yet). Output will land at `data/output/episodes/episode_av_20260626_<ts>.mp4`.
- The concept is **forced all-cuts i2v + locked bg** (NOT ref mode — that was the
  background-mush cause). Persistent copy: `data/output/handoff_0626/plan_abc_concept.json`.

**If the render finished:** find the newest `episode_av_2026062*_*.mp4`, extract frames
(`ffmpeg -i EP -vf fps=1,scale=170:-1,tile=7x4 sheet.jpg`), CHECK: (a) Ryani has NO chest
ring, (b) backgrounds sharp & stable (no within-cut morph), (c) Plan A(모범자세)→B(우다다)
→정색→C(에어컨 발라당)→레오 윙크 arc. Captions get rewritten at render; **add explicit
"Plan B" / "Plan C" labels** via `scripts.recaption_finish` if missing (cut1 already says
"Plan A").

**If the render died (network):** resume —
```
CONCEPT_BRAINSTORM=0 .venv/bin/python - <<'PY'
import datetime as dt, json
from agents.producer import produce_and_render
c=json.load(open('data/output/handoff_0626/plan_abc_concept.json'))
print(produce_and_render([c], dt.date(2026,6,27), dry_run=False))
PY
```

**Swap into the 18:00 slot (replaces SvG-b87PxnQ):**
1. Delete the old self-intro: `.venv/bin/python -c "from youtube.upload import veto_video; print(veto_video('SvG-b87PxnQ', delete=True))"`
2. Upload Plan ABC scheduled-public at 18:00 KST (= `2026-06-27T09:00:00Z`):
   point the new card's `output_video_path` at the final mp4, then
   `_auto_upload_episode(con, Path(mp4), date(2026,6,27), publish_at_iso='2026-06-27T09:00:00Z')`
   (see how I did the self-intro earlier this session — same call; it packages title/tags +
   sets a Giri-picked thumbnail).
3. Verify live: `youtube.oauth.get_youtube().videos().list(part='status', id=<newid>)` → private + publishAt.

## 6/27 board — current live state (verified via API)

| Slot KST | Lane | video_id | What | Status |
|---|---|---|---|---|
| 08:00 | AV | `QlgbeyqkpDI` | 할머니 뽀뽀 (원근+캡션 fix, v3) | private 예약 |
| 12:30 | RF | `jfyqT-7SqAU` | 잠자리 찾기 (reframe) + 레오 자는 썸네일 | private 예약 |
| 18:00 | AV | `SvG-b87PxnQ` → **replace w/ Plan ABC** | self-intro → 여름 에어컨 밈 | render in-flight |
| 21:00 | RF | `E3KbD76D4Fc` | 피자 겨울 집밥 (unchanged) | private 예약 |

## Shipped this session

- **AV8 할머니뽀뽀** (`QlgbeyqkpDI`): the kiss cut rendered a giant looming forearm
  (perspective break). Fix = a perspective-correct still where grandma is at the SAME
  scale/depth as Leo AND MINIMAL (lips + a hand only, face out of frame — PD rejected a
  first still with too much grandma face). cut2 re-rendered i2v from that still; cut3
  trimmed+cropped to the clean meme reaction; captions de-emoji'd (😳😹 broke drawtext —
  only ♥/♡ allowed). Lesson saved in memory `av_seedance_render_quality_lessons` (#4/#5).
- **RF1230 잠자리 찾기** (`jfyqT-7SqAU`): cut1 (Leo's back, 15s) → 5s "탐색" setup; cut2
  sleeping FACE (9s) = the "찾았다 바로 여기!" payoff. 37s→23.6s. Thumbnail set to a clean
  Leo-sleeping frame (cut2 @7.8s). `scripts.recaption_finish` + `reupload_episode`.
- **Ryani harness-ring fix** (PD: "랴니 하네스 고리가 자꾸 등장해"): ROOT = `assets/character_ref/
  ryani_solo.png` (the present-day Ryani real-photo ref the cameraman i2v path uses,
  REF_LIBRARY["ryani_solo"], cameraman ~3382/5227) had a collar+chest D-ring → copied into
  EVERY Ryani gen; the text guard "NO collar/harness" (cameraman ~1490) can't beat an image
  ref. Fixed by editing the ring/collar out (`_generate_scene_openai`) keeping identity,
  swapped in. **⚠️ `assets/character_ref/` is GITIGNORED** — this swap is LOCAL ONLY (not in
  the 379ceaa commit). Backup: `assets/character_ref/ryani_solo_withring.png`; applied
  version also at `data/output/handoff_0626/ryani_solo_cand1_applied.png`. Validated:
  regen with the new ref → ring gone. Memory updated in `av_ref_must_be_real_photo`.

## board bot → tool-using agent (committed 379ceaa)

PD's insight: the fixed intent-menu = whack-a-mole (every new ability needs a hand-coded
intent). Refactored `slack/board_agent.py` so the LLM (Gemini 2.5 Pro) calls **live tools**
and composes the answer itself: `youtube_schedule(date)` (cards + LIVE YouTube verify —
ends the "DB stale / check via CLI" punt), `db_query` (SELECT-only), `read_log`, `get_status`,
`list_knowledge`, `set_concept`, `answer_knowledge`, `escalate`; `veto`/`render` still gated by
the confirm flow. Tested offline ("6/27 예약 video id 슬롯" now answers with live data).
**Bot restarted** (`launchctl kickstart -k gui/$(id -u)/com.rianileo.slack`, pid was 26514).

## Open / NEXT

1. **Finish the Plan ABC swap** (section 1) — the only unfinished content task.
2. **Re-apply the Ryani ref on any fresh checkout/restore** (gitignored; keep `_withring`
   backup if you ever need to revert).
3. **Stale `launch_threads`**: `reupload_episode` / `_auto_upload_episode` update `cards` but
   not `launch_threads` (veto in app.py reads it). The board bot now sidesteps this by reading
   cards+live, but consider syncing launch_threads on reupload for the veto path.
4. Uncommitted nothing else; working tree clean except gitignored assets.
