# Session Handoff — 2026-06-28 (6/27 저녁 ~ 6/28 새벽)

긴 세션. **6/28 공개분 전량 폐기 사태를 4편 재구성으로 살리고**, 그 과정에서 **재발방지를
대량 박고**, **board 봇을 CLI 수준(Claude Opus)으로 + board↔CLI 소통 루프**까지 구축.

---

## ★ NEXT (다음 세션 최우선)

1. **6/28 4슬롯 공개 확인** (라이브 YouTube API로):
   - 08:00 KST `HYY8QGB7jy8` 태풍이(RF) / 12:30 `zRZI-uV4hxE` 에어컨 배만지기(AV)
   - 18:00 `ejtFKv26n4E` 축구(RF) / 21:00 `LL4jtpgB7MM` 간식(AV)
   - 전부 발랄 캡션·도사체 0·컨셉 명확. 공개 후 반응 체크.
2. **★ 6/29 03:00 배치 스팟체크 (가장 중요)** — 오늘 박은 재발방지 게이트들이 **첫 자동 배치에서
   실제로 잡는지** 검증하는 자리. PD "내일은 전량 폐기 안 돼"의 확인. 결함 보이면 veto + 수정.
   배치는 launchd `com.rianileo.launch` 03:00 KST가 6/29분 생성(새 프로세스라 오늘 수정 다 반영, AV pause 아님).
3. **board 봇 CLI 수준 검증** — PD가 슬랙 board에서 "내일 예약분" 류 물어보면 Claude Opus로 날짜
   안 헷갈리고 4슬롯 다, 잘림/누출 없이 답하는지.

## 미완 / 주의

- **board 날짜 프롬프트**: Claude + `_SYS` 명시로 개선했으나 실사용 검증 필요("오늘 새벽 배치=내일 D+1 공개분").
- **AV 시의성 슬롯 추적**: 6/28 낮잠 AV가 왜 `require_timely`를 안 탔는지 미규명(launch가 가장 이른 AV 슬롯에 강제하는데).
- **RF grounding이 실제 사건을 못 잡음**: 태풍이 "오줌→피함" 같은 미묘한 사건을 VLM이 못 읽어 캡션이 겉돎.
  당장은 PD ground-truth 또는 내가 프레임 직접 확인해 캡션 작성. 근본 해결(claim API 자동화처럼) 미완.
- **일회성 스크립트 커밋 제외**: `_finalize_acbelly`/`_new_av_timely`/`_new_rf_one`/`_rerender_rf8_taepung`는
  디버그용이라 커밋 안 함(필요시 재사용 가능, repo에 존재).

---

## SHIPPED

### A. 6/28 전량폐기 → 4편 재구성·예약
원래 4편(낮잠 AV / 카페 RF / 계단 RF / 태풍이 RF)이 **전부 결함**(캡션 도사체 + 각자 결함)이라
Giri 러버스탬프로 자동 예약돼 있던 걸 발견 → 전량 veto(private + publishAt 해제, 가역) → 재구성:
- **RF8 태풍이** (`HYY8QGB7jy8`): PD ground-truth(담장 너머 남친 태풍이가 오줌→랴니 피함→다시 킁킁)로
  발랄 캡션 직접 작성 + recaption($0) + title "남자친구 태풍이". work_dir 클립만 burn해 콜라주 없음.
- **축구 RF** (`ejtFKv26n4E`): 새 제작. "2021 공원 축구 타임"(구체 단일사건) + 발랄 캡션(드리블→골!).
- **에어컨 배만지기 AV** (`zRZI-uV4hxE`): 새 제작. 여름 에어컨 시의성. 배만지기가 still+모션으로 화면에
  제대로 나옴(B 검증 통과). cut4 캡션 "월드컵"→"챌린지"만 수정.
- **간식 AV** (`LL4jtpgB7MM`): 월드컵 간식전쟁본 재패키징(월드컵 단어 빼고 "간식 쟁탈전").

### B. 재발방지 (커밋 `bd12ffb`) — 내일 03:00 배치부터 적용
- **캡션 도사체**: `caption_agent.md`(두 레인 공통 최종단)+`writer_story.md` 발랄 기본화 +
  Giri LLM cap≤6 + **deterministic `_preachy_caption_gate`**(여유란/N년경력/베테랑 프로토콜 등 코드 검출).
  교훈: 생성기만 고치면 최종 작성기+Giri에서 샌다 — **lockstep**.
- **RF 추상무드 컨셉 금지**: `realfootage_concept.md` — "여유/평행/안온함" 무드는 컨셉 아님,
  구체 사건/시간 3택(단일사건 / 하루 vlog / 메모리레인)이어야 클립이 정해짐.
- **era-mix 인과성**: 시점 캡션 있어도 무관 클립 나열이면 결함(생성기+Giri).
- **쇼파 morph**: `director_shots.md` 단일공간 shot_size 일관(한 컷 wide 점프가 배경 재생성) + reviewer 게이트.
- **B(연출) 강제**: `director_shots.md` — **"Seedance는 다 그린다, 핵심 액션은 still(소품/구도)+motion(단계)으로
  연출하라, 캡션-only 금지"**(PD: "시댄스는 다 그릴 수 있어, B의 문제야"). 월드컵 골/세리머니가 붕 뜬 원인.
- **콜라주 풀오염**: 싱크업이 "Ryani&Leo cozy moments" 브랜딩 콜라주를 RF 풀에 유입 → 24개 `[BRANDING]` 마킹.
- **gutted-guard**: prefetch로 이미 줄어든 컷을 원본으로 착각하던 걸 → 원본 컷수 honor(카페 2/5 차단).
- **츄르 모양 canon**: 가느다란 스틱 파우치(치약튜브로 키우지 말 것).
- **dedup 14일**: launch가 같은날만 보던 걸 최근 14일 공개분까지(낮잠 중복 차단).
- **월드컵 시의성 비활성**(PD: 분위기 안 좋음): DB trends 삭제 + `trend_feed.py` worldcup 엔트리 비활성(복구 가능).
- **veto publishAt 버그**: private만 보내고 publishAt 안 지워 재공개 위험 → 검증+재시도.

### C. 인프라
- **BrokenPipe 워치독**(`slack/app.py`): 네트워크 블립 후 slack_bolt가 영구 BrokenPipe 루프에 빠져
  봇이 죽던 걸(프로세스는 살아서 launchd가 못 살림) → 300s 내 BrokenPipe 40회면 `os._exit`로 launchd 재기동.
- **BGM claim 자동동기화**(`sync_bgm_claims.py` + launchd 07:00): 일반 채널은 Content-ID claim을 API로
  직접 못 읽음(youtubePartner 403) → 차단형은 자동탐지 + 공개형은 `/bgm-claim` + 업로드 시점 video→BGM
  영구맵(`bgm_by_video.json`). claimed 원장에 stockaudios(NRA-LAB)·9jackjack8 백필.

### D. board / grandma 봇
- **board → Claude Opus**(커밋 `4fa199e`): PD 1:1 저빈도 surface라 Gemini→**Claude Opus**(anthropic 직접,
  **CLI 세션 무관 24/7 독립**). NO-Anthropic은 대량용 비용가드라 board 예외. Claude→Gemini→cascade 폴백.
- **board JSON 누출/잘림**(커밋 `44f0160`): max_tokens 900→2500 + 잘린 final 복구 + 깨진 JSON 비노출.
- **grandma 답변 짧게**(커밋 `8065e6f`): 1~2문장(할머니·할아버지 큰 폰트 가독성).
- **board↔CLI 공유 진행로그**(커밋 `3076398`): `notes/progress_log.md` + `agents/progress_log.py`.
  executor 완료→`[board]` 기록, board 봇 답 전 `recent_progress` 주입, CLI는 CLAUDE.md 🔁 룰로
  세션 시작 읽기+작업 후 `log_progress('CLI')`. **둘이 같은 맥락에서 소통**.

---

## 커밋 (오늘, 브랜치 approach-d-grounded-singlepass)
`bd12ffb` 전량폐기 수정+재발방지 · `44f0160` board 누출 · `8065e6f` grandma 짧게 ·
`4fa199e` board Claude화 · `3076398` board↔CLI 공유 루프

## 관련 메모리
[[board_executor_and_cost_ledger]] (board Claude+루프 갱신됨), [[rf_caption_quality_fix]] (도사체 재발+게이트),
[[bgm_copyright_swap]] (claim 원장 gap), [[giri_rubberstamps_0623_batch]] (러버스탬프 재발).
