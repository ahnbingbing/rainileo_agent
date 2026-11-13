# Session handoff — 2026-11-13 (Giri PD-거울 + RF 비주얼 그래머: 빌드 → 플립 → 품질 반복)

**스파인:** 지난 세션이 **설계만** 남긴 두 방향(`giri_pd_mirror_design.md`·`rf_visual_grammar_design.md`)을
이번에 **빌드 → 라이브 플립 → PD 실렌더 리뷰로 품질 완결**까지. 둘 다 VM 라이브. 관통 교훈 = **PD가
렌더 결과(와 입력 footage)를 끝까지 봐서** 내 땜질을 걷어내고 근본 레버로 이끌었다.

HEAD `785f146`. 커밋 22개(`git log 3accf2b..HEAD`). 전부 main push = VM 배포됨.

---

## A. RF 비주얼 그래머 — 라이브 (`RF_DIRECTOR_PASS=1`, `RF_SPLIT_MOTION`=코드ON)

PD가 원한 건 iOS 포토앱 Memories의 **화면분할 + 전환**인데 RF가 안 썼다(허용≠행동).

- **길이 그래머 폐기** — 처음 "전환 방식"을 "전환 길이(0.4→0.7s)"로 축소했다가 PD "장난?"(안 보임).
  `RF_TRANSITION_GRAMMAR` OFF로 격하. 교훈 C_visiblelever(보이는 레버를 렌더로 검증).
- **모션 와이프 split**(01413d2·537fe2a) — 상하분할·위←좌/아래←우 와이프·블러 no-black·둘 다 라이브.
  PD "모션이 있으면서 나뉘어야"(정적 그리드 틀림). `_split_motion_wipe_filter`/`_prerender_split_cuts`.
- **Director 능동구성**(01413d2) — `director_realfootage.md`가 split_with/transition_in 소유, `apply_rf_visual_grammar`가
  concept cuts에 적용, render_card가 `RF_DIRECTOR_PASS`로 호출. 허용≠행동 트리거.
- **E2E 플립** — 실 LLM Director가 과거↔현재 split 능동구성 입증. PD가 VM `RF_DIRECTOR_PASS=1` 플립(`!` gcloud).
- **캡션 2결함**(7585124·537fe2a) — ①split 캡션은 then↔now 의미를 Director가 저작(`split_caption`, 픽셀에 없음,
  C_splitcaption) ②cut3 "참는다" 날조 근본=액션 VLM이 1080p컷 240s 타임아웃→**480p 프록시**로 수정(C_vlmpayload,
  렌더 830→480s 보너스).
- **★split 프레이밍 사가**(86f717a·b39eb2c·785f146, C_splitframing) — PD가 렌더 보며 "주인공 안 잡혀"를 다섯 번 잡음.
  센터크롭→좌표크롭→contain-fit→얼굴트래킹 전부 실패. **소스 클립 직접 보니** 와이드샷(펫 작고 바닥)이라 크롭할
  얼굴이 없었다. 결론: **footage 성격(close-up/wide) + 재생속도**가 레버지 크롭이 아니다. Fix: ①prominence
  binary 판정(close-up→cover-crop, wide→contain-fit) ②**패널별 속도**(wide를 1.6×로 재생해 얼굴순간 스캔, PD "딱
  좋아!") ③상류 게이트=`_rf_grammar_cards`가 clip별 shot 태그 VLM판정→Director가 close-up끼리만 split(Director를
  prestage 後로 이동). 킬스위치 `RF_SPLIT_FACE_CROP`·속도 `RF_SPLIT_WIDE_SPEED`(1.6).

**관찰 대기:** 00:00 배치 첫 라이브 split 스팟체크 · match_cut은 2 E2E서 Director가 안 골랐음(situational, 라이브 관찰).

## B. Giri PD-거울 — 라이브 (`GIRI_PD_TASTE=1`·`GIRI_PD_LENS=1`, cron 설치)

★근본(실측): `pd_taste`(pd_selections) corpus가 **이미 있는데 제너레이터만 소비·Giri 검수기는 블라인드**
(review-learning이 생성기만 갱신·검수기 미sync=러버스탬프 drift의 **코드 증거**). "먹이기 6월 끊김"은 **stale 로컬 DB
오진**(veto 로깅은 VM서 계속 돔, WHERE-THIS-RUNS).

- **P0**(92ca48b) 소비=`reviewer.py`가 `taste_digest(for_review)` 주입 + 먹이기 보강(`/veto <id> <이유>` 캡처).
- **P2**(2ab4baf) `PD_LENS_BLOCK`=PD 판단방법(skeptical·캡션↔footage먼저·근본>표면), `GIRI_PD_LENS`.
- **P3**(2ab4baf·8c58d5a) `agents/giri_audit.py`=corpus+게이트→LLM 갭리포트만(자동수정금지). 주 cron(월11:00)+세션시작
  훅이 `giri_audit_latest.md` surface. 스모크=얇은 corpus서도 실갭 6건.
- **P1 피벗**(851b19a, PD "왜 슬랙 기반?") — Slack 스레드 리더는 틀린 우물(라이브 35스레드 0코멘트). 폐기+**세션-피드백
  캡처**: git-추적 `notes/pd_corpus.jsonl`(세션=로컬DB·Giri=VM DB라 git파일이 전송), 스킬이 거기 append.
- **P0.5**(823c1be, PD "판단해서 저장, 플레인 텍스트by텍스트 금지") — ①`distill_and_append`=LLM이 PD 원문 판단→원칙
  한 줄 축약 저장(synthesize-don't-transcribe를 corpus에도) ②**Giri가 판단하며 저장**=`reviewer.review()`가 verdict/
  발동게이트/결함수를 `giri_reviews` 테이블에(`GIRI_SAVE_JUDGMENT`).
- **러버스탬프 조인**(590543d) — `giri_audit`가 `giri_reviews`(Giri PASS)↔`pd_selections`(veto)를 cards로 조인해
  "Giri 통과→PD veto" 실증을 갭리포트에. (P0.5가 저장만 하고 안 읽던 루프 닫음.)
- **스킬 배선** — `giri-update`·`review-learning`의 "Record" 단계가 `distill_and_append`로 PD 원문을 증류 저장.

**VM 상태 확인:** `pd_selections` 24행(로컬 12 stale), 마지막 veto 8-29 = corpus 얇음(세션 캡처로 앞으로 두텁게).

## 남은 것 / 다음
- **관찰**: 00:00 배치 split 첫 라이브(Director가 close-up끼리 split하나·프레이밍·match_cut) · 월11:00 첫 Giri 갭리포트
  · Giri가 PD 기준으로 더 깐깐한지.
- **하드닝 후보**: P1 세션캡처는 스킬이 수동 호출(내가 세션서 `distill_and_append` 불러야) — 자동화 여지.
  `giri_reviews` 쌓이면 러버스탬프 추세 대시보드.
- **플립 명령**(PD가 `!` gcloud로): `RF_DIRECTOR_PASS=1`·`GIRI_PD_TASTE=1`·`GIRI_PD_LENS=1` = deploy.env 추가+봇 재기동.
  crontab 재설치=`crontab -u rianileo deploy/crontab.vm`. (전부 실행 완료.)

## 교훈(회고 기록)
- **C_visiblelever** — 보이는 미감 변경엔 보이는 레버, 렌더로 눈 검증(유닛 수학 아님).
- **C_splitcaption** — 합성 편집 캡션은 그 의미를 아는 쪽(Director)이 저작, 픽셀에 없는 건 VLM 못 씀.
- **C_vlmpayload** — 네이티브 VLM 타임아웃은 재시도 전 **페이로드부터 줄여라**(프록시). fallback 데이터 실재 확인.
- **C_splitframing** — VLM은 'A냐 B냐'엔 강하고 '어디냐'엔 약하다(분류 시켜라). 움직이는 피사체엔 크롭 아닌 **속도**가
  레버. 표면 반복 조정 전 **소스를 직접 봐라**.
- **B_pdmirror** — 검수기도 생성기와 같은 ground truth 읽어야 거울. 운영 상태는 VM에 물어라(로컬 stale). 학습저장소엔
  **증류된 판단만**(raw 덤프 금지). 검수기는 자기 판단을 남겨 PD와 대조돼야 교정된다.

cf 메모리 [[giri_pd_mirror_build]]·[[rf_split_motion_wipe_spec]]·설계 `giri_pd_mirror_design.md`·`rf_visual_grammar_design.md`.
