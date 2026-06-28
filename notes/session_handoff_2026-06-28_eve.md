# Session Handoff — 2026-06-28 (저녁/밤 세션)

아침 세션(전량폐기→4편 재구성)에 이은 같은 날 두 번째 긴 세션. **6/29 4슬롯을 PD 피드백대로
다시 손보고**, **grandma 봇을 대수술**하고, **AV 화질 근본수정(소스 정밀화 1·2·3 + 에이전트
3종)**까지 끝냄. 전부 push 완료(approach-d-grounded-singlepass).

---

## ★ NEXT (다음 세션 최우선)

1. **★★ 6/30 03:00 배치 스팟체크 (가장 중요)** — 이번 세션의 AV 근본수정이 **첫 자동 배치에서
   실제로 먹는지** 검증하는 자리:
   - 배경에 베이크된 펫/잡것 없는지(Fix1 scene_ref 빈방), 정밀스틸+i2v로 순간이동·배경 freelance
     없는지(Fix2, **default ON**), 단독컷에 다른펫 없는지(Fix3 cast).
   - 페이오프가 자기 컷으로 나오는지(Director 비트커버리지), Giri가 결함 잡는지(캡3).
2. **grandma 봇 안정성 확인** — 오늘 네트워크 micro-drop으로 봇이 3번 wedge돼 무응답. 워치독
   4/120s + 재시작 catch-up(텍스트+파일)으로 자동복구하게 했으나 **근본은 맥 네트워크**. 재발 잦으면
   GCP 이전(아래) 가속.
3. **giri-update 정석 회귀검증** — 새 Giri 캡 3종(펫순간이동·off-cast·페이오프미발생)을 알려진-결함
   영상(관찰왕 v2)으로 회귀 테스트(네트워크 안정 시). LLM 캡이라 미검증.

## 6/29 공개분 4슬롯 (전부 private+예약, 최종본)
- 08:00 삼계탕 AV `ftewhl_7IXs` — 인과스토리 + 박쥐귀 + 김인과(intro 펄펄→먹는컷 김없음) + 엔딩캡션 "복날 대작전 완료!"
- 12:30 수영장 RF `-_kMDwcUr3g` — "첫 수영" 거짓 제거 → 진행형
- 18:00 관찰왕 AV `tnmTL1wlnyM` — 재회 페이오프 명시컷 + 배경유지 + cut2 "스크래치로 간 레오" 캡션(PD가 배경 레오 짚음)
- 21:00 카페 RF `-8vKEvedtrw` — 2026-06-26 진짜 카페 나들이(레오 실존·날짜일관)

---

## SHIPPED (이번 세션)

### A. AV 화질 근본수정 — PD "스틸을 정교하게" 방향 (소스 정밀화)
오늘 버그(레오 순간이동·배경 잡것)의 진짜 root는 Seedance 환각이 아니라 **소스 이미지**였음.
- **Fix1** (`0fe9c1b`): scene_ref 배경=빈 방 강제. `set_library[home_livingroom]`이 레오 자는
  실사진(bg_b969c2ad)을 가리켜 모든 거실 컷 배경에 그게 따라붙음 → 고양이 인페인팅 제거한 깨끗본
  `home_livingroom_clean.png` 생성+repoint + `_scene_ref_is_clean` VLM가드(path+mtime 캐시, fail-open,
  `SCENE_REF_CLEAN_CHECK=0`로 끔). 전 set_library scene_ref 감사=CLEAN.
- **Fix2** (`be25400`+`a25ff91`+`67b4d49`): 정밀스틸+i2v. AV still을 깨끗한 scene_ref를 **literal
  배경으로 + 캐릭터ref 위치대로 합성**(gen_still_multiref) → i2v가 충실히 애니메이트(ref freelance
  대신). `_compose_av_still`/`_cut_char_refs`/`_av_still_compose_prompt`. **default ON**(`AV_PRECISE_STILL=0`로
  끔). 최적화: 정밀컷은 generate_batch(느린 gpt-image) **생략**(필터 매니페스트)·Gemini 합성만. set_anchor
  스코프버그 픽스. 풀렌더 검증 완료(관찰왕).
- **Fix3** (`e53f020`): cast-explicit. `_av_still_compose_prompt`가 단독 컷이면 "다른 펫은 이 프레임에
  없음" 명시 + `still_select` FLOOR0에 off-cast 실격(Subjects 의도 대조).

### B. 에이전트 수정 (상류 품질, `e9ae507`)
- **director_shots.md**: ①모든 스토리 비트=컷, **페이오프는 자기 컷 필수**(관찰왕 v2가 story_arc엔
  재회 있는데 컷으로 안 만들어 "문 열고 아무것도 안 함") ②컷간 피사체 위치 연속성(순간이동 금지).
- **reviewer.py Giri 캡3**(ai_vtuber/both): 펫순간이동·위치불일치 ≤6 / off-cast 펫 ≤6 / 빌드업 후
  페이오프 미발생(구조적) ≤6·수정필요. Director와 lockstep.

### C. grandma 봇 대수술 (PD 연속 피드백)
- 쓰레드 금지(채널 top-level만) / 답변 **한 문장** / 랴니·레오 **분류 안 시킴**→"무슨 영상이에요?" 내용질문 /
  **중복·이미 ingest된 파일 업로드 침묵 버그** 수정(기존 에셋 반환+INSERT OR IGNORE) /
  **nudge 복구**(ModuleNotFound)+아침8시+저녁7시(영상 無시 "오늘 바빴나봐요" 톤) / 함미·하비라 부르며 안부 /
  **질문=컨텐츠 끌어내는 수단**(자양분, episode_stories), 얻으면 "고마워요!"로 닫기, 늦은밤 "이만 주무세요".
- **BrokenPipe 워치독 4/120s**(40/300s→12→4; wedge가 reconnect당 1개씩 천천히 내서 못 잡던 것, 정상=0이니
  공격적) + **재시작 catch-up**(`_grandma_catchup`): 끊긴 사이 Slack이 replay 안 해주는 메시지를 (재)시작 시
  채널기록 읽어 "마지막 봇답장 이후" 가족 메시지(텍스트+**파일 업로드**) 자동 처리·답장.

### D. board 봇 정직화 (`07e17f1`)
봇이 "재렌더 escalate하겠다" 약속해놓고 executor는 유료키 없어 렌더 못 함 → 시스템프롬프트에 executor
한계 명시(코드/프롬프트/데이터 수정만, 렌더·업로드·재렌더 불가)+약속 금지, escalate 메시지를 "코드수정=
executor, 실제 재렌더·재업로드=CLI 세션"으로 정직화.

### E. board-executor 병렬작업 회수 (`96ccf6d`)
내가 executor 죽인 뒤 launchd가 #15/#16 재처리해 같은 4편 durable 게이트를 만들어 board에 응답함(나와
상보). 그 커밋 안 된 변경 검증·커밋: director_shots i2v→ref강제, producer `_rf_temporal_coherence`
pet_exists_on 게이트E/F(2024 레오 거짓 차단), reviewer `_false_first_water_gate`(첫수영 거짓), canon 물매니아.

---

## 미해결 / 주의

- **★ 맥 네트워크 불안정** = 오늘 봇 wedge(3회) + 렌더 hang의 공통 원인. catch-up+워치독으로 단기 자동복구.
  **근본 해결 = GCP 이전**(다른 세션이 계획 LOCKED: e2-medium@서울, shadow봇, sqlite유지, 봇 연결검증→1주
  shadow→원자컷오버, 선행=icloud/sync.py를 ingest_local[Mac]+ingest_register[GCP] 분리). 유일 macOS종속=
  iCloud인입(osxphotos, 자산16558 vs slack43). 봇·cron·렌더·DB는 클라우드 가능. 진행로그로 두 세션 동기화 중.
- **Fix2 렌더시간**: 정밀컷 gpt-image 생략으로 줄였으나 Gemini 합성+i2v라 여전히 컷당 수분. 배치 시간 모니터.
- **일회성 스크립트 커밋 제외**: `scripts/_rerender_{samgyetang,gwanchalwang,cafe}.py` 등 디버그용(repo 존재, 미커밋).

## 관련 메모리
[[av_source_precision_fixes]] (Fix1·2·3 신규), [[grandmompapa_bot]] (대수술 갱신), [[board_executor_and_cost_ledger]]
(정직화), [[session_handoff_2026-06-28]] (아침 세션).
