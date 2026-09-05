# Session handoff — 2026-09-05 (RF "왜 못 만들었나" → 진짜 병목은 비용/재전송이었다)

**스파인:** "RF가 안 만들어졌다"·"비용이 안 줄었다" 둘 다 **표면이 진실을 가렸다.** ①"신선 풀이
말랐다"는 내 진단은 **틀렸다** — 풀은 946개(post-Leo 610)로 크다(PD "많잖아"가 맞았다). ②게이트·생성기
신호(avoid-list·reuse-feedback·location-bias)는 **이미 다 구현돼 정상 작동**했고, RF는 설계된 graceful
AV/memory-lane 폴백으로 빠진 것(버그 아님). ③"OpenAI 콜이 안 줄었다"의 범인은 9/4에 줄인 gpt-image가
아니라 **텍스트 캐스케이드**였고, 거기서도 **콜 수(234)가 아니라 프롬프트 크기(81콜 avg 103K = 90%)**가
진실이었다. **교훈: 표면(풀 크기·image 비용·콜 수)이 아니라 원장/프레임/ledger 토큰 분포가 진실이다 —
전제부터, 측정부터.**

## VM authoritative · push=deploy
- **VM HEAD e472e70** (오늘 4 durable 배포·pull 검증). `git push origin main` → deploy.timer 2분 폴 pull.
- SSH가 IAP 터널로 매번 ~15-20s → 조회를 한 명령에 묶어라. prod 렌더 kill 불가 → `render_control.request_stop()`.
- **VM DB는 `sudo -u rianileo`로 열어야** 함(0600 소유). 로컬 스크립트 못 넘김 → `python - <<PY` stdin.

## SHIPPED — durable (오늘, 커밋순)
1. **Writer truncation 재시도** (defcf61, D_writertrunc): `writer_director._call_anthropic`이 max_tokens
   truncation을 provider 장애로 오취급→OpenAI 45s 타임아웃(16k JSON 생성 불가)→Gemini 504→non-JSON→legacy
   (오늘 non-JSON 61·타임아웃 15). Fix=truncation이면 Anthropic max_tokens 2배 escalate 재시도(cap
   `WRITER_MAX_TOKENS_CAP`=32000), circuit은 truncation엔 UP 유지·진짜 장애만 down. 단위검증 3케이스.
2. **텍스트콜 호출자 비용 귀속** (256f675): `_log_text`가 flat `stage='cascade'`로 234콜을 뭉쳐 어느
   함수가 100K 프롬프트를 보내는지 안 보였음. `_caller_label` 스택워크로 `module.func` 기록(CURRENT_STAGE
   env 우선). 다음 배치가 범인 함수 특정. best-effort.
3. **RF 프롬프트 캐싱** (e472e70, D_rfcache): RF 단일패스를 정적(system+풀+규칙) vs 동적(Giri feedback)
   분리, 정적을 `llm_cascade.call_text_cached`(Anthropic-primary system `cache_control=ephemeral`)로 →
   재제안 재전송분을 cache_read(~90% off). 실패 시 cascade 폴백. `RF_PROMPT_CACHE=0` revert,
   `RF_CACHE_MODEL`=claude-sonnet-4-6. **라이브 검증: cache_write 127K $0.50 → cache_read 127K $0.07(86%↓),
   풀 바이트 동일 완벽 히트, Sonnet 출력 정상.**
4. **회고**(3150d6b + 추가): §4.5 D_writertrunc·D_rfcache.

## 진짜 근본 규명 (수정보다 진단이 핵심이었던 날)
- **RF "9/7 못 만듦"** = 신선 공급 문제 아님. 실제 풀 dur≥12·VLM완료·quality≥0.7 = **946개(post-Leo 610)**.
  파이프라인이 writer 앞에서 946→`_diversity_sample(100)`→필터(−이웃9·−최근공개25)→`_cap_overused_locations`
  61→33으로 **의도적으로** 붕괴(프롬프트 크기캡·dedup·과대표집 방지). 33개는 다양했으나 writer가 홈 씬 재사용
  →검수기 clip-reuse(중복률 1.0, 9/2·9/3 홈/거실밤) 정당 반려→3-try 캡→AV/memory-lane 폴백(9/7 3/4 채움,
  12:30·21:00은 삐용이 memory-lane). ★avoid-list·reuse-feedback·`_lead_with_underused`·`_cap_overused_locations`
  전부 이미 배선됨 — "생성단 다양성 신호 주입"은 redundant.
- **클립당 필드는 load-bearing** — `sc`/`content_desc`/`observed_motion`을 트림하려다 realfootage_concept.md가
  "이게 진실이다 / sc에 없으면 캡션 금지"로 쓰는 anti-hallucination ground-truth임을 발견, revert. 낭비는
  크기가 아니라 재전송(→D_rfcache).
- **비용 정밀 귀속**: 오늘 OpenAI 텍스트 234콜 $37. 토큰 버킷 `<5k` 119콜 $1.2 vs `≥50k` 81콜 $33(avg 103K,
  max 183K)=90%. 범인=RF 단일패스(콜당 92K)×재제안 churn.

## 조사만 (수정 대기, PD 결정/후속)
- **인입 정지**(신선 공급의 진짜 병목): iCloud "Ryani & Leo" 앨범 **8/24부터 12일 신규 0**. 라이브러리엔
  8/24 후 **146개 신규(비디오 30) 있으나 앨범 태깅 0** → 파이프라인이 못 봄. **앨범 태깅 복구=신선 비디오 30
  즉시 인입** = RF 공급 근본 해결. ★PD 액션(사람/Photos 앨범, 코드 아님). notes/ingestion_rate_drop_2026-09-04.
- **9/7 08:00 슬롯 빔**: 원래 AV 슬롯, Giri 6회 실패로 비움. 손수정 대기(memory-lane RF or AV).
- **RF_CACHE_MODEL 튜닝**: Sonnet은 저-churn 슬롯 near-break-even. Haiku 4.5면 2콜에도 65%↓지만 스토리
  품질 트레이드오프(PD 결정).

## gotcha (오늘 값비싸게 배운 것)
- **표면 ≠ 진실 3연타**: 풀 크기(raw 1022 vs writer가 본 33)·OpenAI 비용(image vs text)·콜 수(234 vs 81
  거대콜). PD 도메인지식("많잖아")이 내 aggregate 진단을 이겼다 — 구체 케이스를 ledger/로그로 끝까지.
- **비싼 프롬프트가 load-bearing일 수 있다** — 트림 전 소비자(프롬프트가 그 필드를 이름으로 읽는지) 확인.
- **캐시-miss가 오히려 비쌀 수 있다**(Sonnet write 1.25× > gpt-4.1) → 라이브서 cache_read 실제 히트 검증 필수.
  풀이 바이트 동일해야 히트 → 재제안이 같은 context 재빌드하는지 확인(했음, write=read 127057 일치).
- 관찰용 probe 렌더는 제안 후 `systemctl stop`+`request_stop()`으로 Seedance 전 중단(비용 0), **끝나면
  `render_control.clear_stop()` 필수**(안 지우면 6h TTL로 prod 렌더 막힘).
- systemd-run 단일 RF fill: 슬롯이 다른 레인(AV) 배정이면 launch_selfheal이 10초 만에 no-op 종료.

## ★NEXT
- **다음 03:00 배치 = 4 durable 첫 실전**: ①`anthropic_text_cached`의 cache_read 비중(재제안 캐시 히트)
  ②OpenAI 텍스트 $ 감소(원장) ③writer non-JSON/타임아웃 감소 ④호출자 귀속으로 잔여 비용 top-caller 확인.
  **PD에게 배치 후 리포트 예정.**
- **PD 결정 대기**: iCloud 앨범 인입 복구(최대 레버)·RF_CACHE_MODEL(Sonnet vs Haiku)·9/7 08:00 손수정.
