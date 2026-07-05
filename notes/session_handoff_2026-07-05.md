# Session handoff — 2026-07-05 (GCP cutover hardening + bad-episode root-causes)

The through-line: the brain runs on the GCP VM now (`rianileo-brain`), and this session
was **making the migrated pipeline actually work end-to-end on the VM** — because the
cutover silently dropped everything the old Mac environment provided implicitly. Then PD
caught a bad RF episode that exposed a render bug + a reviewer rubber-stamp, plus several
freshness/canon directives. All fixes are committed to `main` and deployed (push = deploy).

## VM authoritative. Deploy = `git push origin main` → deploy timer (2min) pulls → smoke → restart bot.
Verify a deploy actually landed by checking the VM HEAD, not just the push (a silent smoke
failure looks green). VM at HEAD `bdd0c47` end of session.

## 1. Migration gaps fixed (8) — "이관의 진짜 위험은 옛 환경이 암묵적으로 제공하던 것들"
Each looked fine until the code path was actually exercised:
1. **httpx 0.28** removed `proxies` → anthropic client init TypeError → every LLM dead. Pinned `httpx==0.27.2`.
2. **openai / google-genai / google-generativeai** never in requirements (Mac had them out-of-band). Pinned + installed.
3. **google-cloud-storage** missing → all GCS fetch/upload failed silently. Pinned + installed.
4. **DB held 16,612 absolute macOS paths** (`/Users/ahnbingbing/…`). Migrated → relative `data/assets/…`; write sites store relative; `icloud/gcs.py` gained `asset_rel`/`local_path` + robust `blob_name`/`download_to`. Consumers re-root relative → work.
5. **gitignored required files** not in the clone: `data/concept_card_schema.json` (git-tracked now), libraries/ledgers/caches (seeded to VM), bumpers + 325MB BGM (seeded), YouTube OAuth creds (Secret Manager → bootstrap fetch).
6. **ffmpeg 5.1 (apt)** lacks drawtext `text_align` → captions failed. Installed BtbN static ffmpeg to `~/.local/bin`, first on PATH (run_job.sh/crontab/bot service). Also fonts to `~/Library/Fonts`.
7. **Deploy silently smoke-blocked**: `/etc/rianileo/env` was `600 root:root`; the deploy user's smoke.sh sources it directly → every push failed the gate, kept old commit. Fixed to `640 root:rianileo` + bootstrap. Smoke now also inits the anthropic client + imports openai/genai (import-only misses client-init/lazy-import failures).
8. **board executor** was `analyze` (read-only) on the VM (Mac had `auto`). Enabled via crontab `BOARD_EXEC_MODE=auto`; PD-authored escalations may run capped paid renders.

## 2. Bad RF episode (260706_RF0800, deleted) — two root causes
- **Rotation double-rotation (dc1520e)**: a portrait iPhone clip stored 1920×1080 + rotation=−90. The trim baked rotation with a manual transpose but LEFT the stale display-matrix on the output → burn_captions + assemble auto-rotated it a SECOND time → sideways. On modern ffmpeg default autorotate applies rotation before crop AND strips the matrix → no stale metadata, no double-rotation. **Removed the manual transpose; rely on autorotate.** Verified upright. Affects most RF (rotated iPhone footage).
- **Giri VLM rubber-stamp (bdd0c47)**: passed it 8/10 with "웅장" over a sleeping cat, despite CHECK 0 already forbidding claimed-motion-on-static (line 87 worked example). More prompt text won't stop a rubber-stamp → **deterministic grandiose-register gate** (RF-scoped: 웅장·장엄·서사시·전설의… → cap ≤6). Playful energy/locomotion ("우다다 출동") is fine and excluded.

## 3. PD directives shipped
- **Freshness = same VIDEO, not theme** (reviewer_macro): `visual_overlap` now counts a repeat only on exact same-asset reuse or a near-identical pixel-dup — NOT (loc,activity) theme match ("시간이 다르면 소재가 같아도 다른 내용"). `motif_overlap` demoted to advisory. Theme variety is a SELECTION concern (broader pool).
- **아기 랴니 era-allowed**: honorific gate only flags 아기/꼬맹이 랴니 when NO past-era marker; 막내/신참 랴니 + Leo-as-elder stay hard.
- **RF marking check skipped** for real_footage with no Seedance cut (real Ryani — pixel heuristic false-flags).
- **AV=RF photoreal is fine** (Giri must not penalize lo-fi photoreal AV).
- **board bot = top admin**: `rerender` tool re-renders a slot + replaces its scheduled video, immediate.
- **Review UX**: the day's 4 episodes post into ONE batch-summary thread; veto by label (`veto 260705_RF2100`).
- **Friendly GCS names**: `output/episodes/YYMMDD_<LANE>HHMM.mp4` keyed on the publish slot; 30 backfilled. Also pulled to a local review folder was interrupted — see NEXT.

## NEXT
- **RF still needs a passing draft**: with rotation + freshness + honorific + marking + grandiose all fixed, RF should pass now, but the last runs kept cycling on caption-vs-clip (CHECK 0) + the writer not always landing a clean draft. Re-run one RF slot on `bdd0c47` and confirm it passes → GCS `260706_RF0800.mp4` → YouTube schedule (creds work; a validation upload `1oR1hcnK04A` was deleted).
- **AV validation not done** — PD wanted 1 RF + 1 AV before all 4. AV lane untested this session.
- **Local review mirror** of GCS `output/episodes/` (PD wants the _review files locally, easy to check) — the `gcloud storage rsync … data/output/gcs_episodes/` was interrupted; finish it or wire a helper.
- **icloud-sync → VM DB registration**: the Mac dawn sync added 9 new assets to the MAC DB + GCS, but they're NOT in the VM DB (ingest_register still unbuilt). New footage won't reach the VM until registered.
- **Caption↔video semantic mismatch** beyond the grandiose register is still the VLM's CHECK 0 (which rubber-stamps); add deterministic sub-checks as PD flags specific patterns.
