# Session handoff — 2026-11-03 (Gemini Pro 마이그레이션 + RF 스토리텔러 구조 근본수정)

**스파인:** PD가 "AI Studio Gemini 2.5 Pro → 12/1 3.8 Flash 리다이렉트" 공지를 가져옴 → 마이그레이션 후
"RF 영상을 실제로 빌드해 전체 파이프라인 확인" → 스모크 렌더가 **RF 스토리텔러의 두 겹 구조 결함**을 드러냄
(출력 runaway 잘림 + 규칙 3개 충돌로 결말 소실). 셋 다 고쳐 배포. ★공통 교훈 = **작동하는 재시도/폴백/
하류수리가 고장난 주력을 가린다** ([[D_streamguard]] 스파인 재발). 회고 D_modelredirect·C_rfcontract.

## 라이브 (VM `/home/rianileo/rianileo-agent` HEAD = 2505b35, 확인됨)

1. **Gemini Pro 마이그레이션 (2c296bc)** — 리다이렉트는 에러 없는 다운그레이드: 캡션 VLM은 바로 Flash 약점
   (가려진 레오 미검출) 때문에 Pro로 올린 자리. `models.GEMINI_PRO`(env `GEMINI_PRO_MODEL`, 기본
   **`gemini-3.1-pro-preview`** — `gemini-pro-latest` 별칭 대신 명시 핀) → `VLM_CAPTION`·`GEMINI_TEXT`·
   cameraman/board_agent/room_extractor 하드코딩 제거, `check_canon.py`가 `gemini-2.5-pro` 재등장 차단.
   function calling은 레포 어디에도 없음(영향 0). thinking: Pro는 budget 0 거부·-1 전 티어 OK → 기존
   "flash면 0, 아니면 -1" 분기 유효. VM `/etc/rianileo/deploy.env`·`env`에 GEMINI*/VLM_CAPTION 오버라이드 없음 확인.
   스모크 렌더에서 3.1 Pro 캡션/그라운딩 콜 5건 정상(그라운딩 게이트 오캡션 1건 교정).
2. **RF 출력계약 (5cb2b7f)** — 라이브 프롬프트는 `realfootage_concept.md`(★`realfootage_singlepass.md`는
   미사용 dead file로 보임). "STEP 1 각 클립 읽기·20개 self-check 전부 YES여야 출력" + "Output ONLY JSON" +
   호출부 낡은 "Follow the 5-step order" 모순 → Sonnet이 단계를 산문 출력 → 수백 클립 풀에서 12k 토큰 다 태워
   잘림(2/2, 3분+), 24k 재시도는 재굴림일 뿐. Fix=STEP은 생각 순서·응답은 `[`로 시작하는 최종 배열 1개 →
   3/3 JSON-only ~40s. `llm_cascade._dump_truncated`가 잘린 출력을 `data/output/artifacts/truncated_*.txt`에 저장.
3. **RF 한 컷=한 클립 + 실제 길이예산 (7dbdf5a)** — 프롬프트 "최소 5컷·상한 없음" × collapse가 떨어진 동일클립
   컷까지 첫 슬롯에 합침 × 10/29 본문 18s 캡이 뒤 컷 버림 → Writer가 4/4 클립 재사용, 클로저 캡션이 cut2로 이동,
   스모크는 4컷 중 1컷만(제목이 화면에 없는 바위틈·물가 약속, Giri 9/10 통과). Fix: 프롬프트(v1·v2·legacy)
   distinct-clip + 예산(`_rf_body_budget()`=RF_BODY_MAX_SEC를 유저메시지에 주입, ~3-4컷: ~5s 중간+6-8s 마지막)
   · `[구조검증]` 게이트(떨어진 재사용/예산초과 → 1회 재제안, `RF_STRUCTURE_GATE=0` 가역) · collapse 인접 run만
   · `_rf_cap_cuts`가 마지막 컷 예약. 라이브 3/3 = 3 distinct·18s·게이트 불필요·24-28s.
   `scripts/_rf_structure_regress.py` 13/13 + parser/v2/title-cast/diversity regress PASS.

## ★NEXT

- **다음 00:00 배치 = 3개 fix 첫 실전.** 배치 스레드 스팟체크: RF 3~4컷인지 · 클로저로 끝나는지 · 제목↔화면
  일치. 구조 fix는 concept 단계만 라이브 검증 — 오늘밤이 첫 E2E 렌더. 잘림 발생 시 VM `truncated_*.txt` 확인.
- **컷 수 감소는 의도됨** — 10/29 캡 이후 실질 3-4컷이었던 걸 명시화. PD가 더 많은 컷을 원하면 RF_BODY_MAX_SEC
  (길이)와 함께 결정할 문제(컷 수만 늘리면 다시 결말이 잘림).
- **후속(블로킹 아님):** ①Giri가 제목↔화면 불일치를 못 잡음(스모크 9/10) — 로컬 실행에선 reviewer가 DB_PATH를
  안 따라 "no such table"(컨텍스트 결손일 수 있음, 프로덕션에서 재확인) ②stable 3.x Pro 나오면 GEMINI_PRO_MODEL
  교체 ③`realfootage_singlepass.md` dead file 삭제 검토 ④같은 "단계형 프롬프트+JSON-only" 패턴이
  senior_director·edit_grammar_writer(call_text_cached)에도 있는지 점검 ⑤로컬 main 체크아웃이 69c2caf —
  미커밋 progress_log 줄 때문에 ff 막힘.
- MV 레인(11/1 핸드오프)은 이번 세션에서 안 건드림 — 할로윈 MV·산책냥 곡 pick·MV 에이전트 그대로 대기.

## Gotchas (이번 세션)
- VM 레포 경로 = `/home/rianileo/rianileo-agent` (not /opt, not ~). env = `/etc/rianileo/deploy.env` + `/etc/rianileo/env`.
- auto-mode가 VM 프로덕션 읽기(gcloud ssh) 차단 → PD 터미널에서 실행.
- 워크트리엔 data/·gitignored assets(bgm/branding/character_ref) 없음 → 로컬 렌더는
  `DB_PATH=<main>/data/agent.db` + data/assets·assets 심링크. reviewer는 DB_PATH를 안 따름.
- `timeout` 명령 macOS에 없음.
