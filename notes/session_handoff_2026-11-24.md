# Session handoff — 2026-11-23~24 · RF 생산 근본 연쇄 수정 (both-pet 빈 슬롯 → fresh-exhaustion → era 텐션)

**한 줄:** 11/22 RF 전 슬롯이 비던 문제에서 출발해, PD 피드백을 따라 **선택·그라운딩·신선도·era 파이프라인의
근본을 한 겹씩** 벗겨 수정했다. 핵심 관통 교훈 = **희소자원 제외는 *단위*(파일→트림)·*출처*(시도→방영 uploaded=1)를
검수기와 일치시키고, 선호는 공급 쪽에서 bias(거부 아님), LLM이 못 맞추는 소프트 규칙은 결정론 삽입으로 집행**. 전부
main 배포(deploy.timer). 대부분 실측/유닛 검증, 마지막 era auto-label의 VM E2E만 SSH rate-limit 회복 후 남음.

> 이전 핸드오프: `notes/session_handoff_2026-11-20_mv_pipeline.md`. 운영 진실·배포: CLAUDE.md "WHERE-THIS-RUNS".
> 상세 교훈은 `notes/retrospective_2026-05_to_07.md` §4.5·§4.9·§4.10에 영속(이 문서는 세션 요약·resume 포인터).

---

## 배포된 수정 (내 커밋, 시간순) — 전부 killswitch, RF-scoped

| # | 근본 | 커밋 | 검증 |
|---|------|------|------|
| 1 | **both-pet 게이트가 풀 없이 배포돼 11/22 전슬롯 빔** — stopgap으로 요구게이트 즉시 OFF | `70bee0f` | 생산 복구 |
| 2 | **both-pet 공급 floor** (신선 both-pet을 diversity sample 너머로 풀에 보장) | `ec32730` | VM 풀 both-pet 28→39→51(신선) |
| 3 | pool-build 위에서 both-pet 스택 재가동 | `e8f04e6` | 제안 신선 both-pet 4컷 |
| 4 | **PD 교정: both-pet=우선순위≠요구** → 요구게이트(ENFORCE+GIRI_BOTHPET_GATE) OFF, 공급 floor만 ON. 솔로도 ship | `3351f59` | E2E ep1 Giri 9/10 (bO_2sVIeuPQ) |
| 5 | **both-pet floor 신선전용** — 아카이브 재admit이 선대묘'원두' era-mix 유발 → `_arch_field` 제거 | `9c27d49` | — |
| 6 | **era 마커는 제목 아니라 번인 캡션에** landing | `104815f` | — |
| 7 | **era-mix 레버** — 시점마커 규칙을 실제 타는 `realfootage_singlepass.md`에 통합 + 사전렌더 재시도 `RF_ERA_MAX_TRIES=3` | `7de84ae` | — |
| 8 | **iCloud 전파 freeze** — 전체스냅샷 단일PUT이 60MB서 120s 타임아웃→GCS 스냅샷 11-20 동결→VM 사진 11-14. resumable 청크업로드 | `79a3627` | VM 사진 11-14→11-22 복구 |
| 9 | **pd_notes "[맥락]" 오염**(132중 32=24%, 원본과 모순) → `canon.clean_pd_notes`로 3 그라운딩 surface(Writer·cameraman·Giri) strip | `b88e3aa` | 하크니농장 정상 |
| 10 | **활동-카테고리 신선도** — `_recent_la_usage`가 location_type=null 클립 제외(맹점) → 활동단독 추적 추가 | `b88e3aa` | "재탕" 발화 |
| 11 | **소수연도 "6.4년"** → 자연어 "6년 반 전/6년 N개월 전"(`human_span/elapsed_phrase`) + 번 scrub | `b5a2c0e` | "3개월/8개월" 확인 |
| 12 | **fresh-exhaustion 근본 = whole-file+attempt 과도제외** — ⓐ all-time 파일쿨다운이 방영파일 통째 영구제외(검수기는 이미 segment-aware) → **트림단위**(free window 남으면 유지) ⓑ `_clip_reuse_counts`·`_recent_la_usage`가 실패·junk 카드까지 집계 → **uploaded=1 대사** | `c705151` | "세그먼트 재사용 3개 안 쓴 구간 유지" 발화 |
| 13 | **아기-레오 era-mix 텐션** — 레오 급성장→both-pet 거의 항상 era-mix→Writer 자가라벨 실패→무한 grind. **결정론 era-label 삽입**(`_autolabel_era_mix`: 아기레오 시절/N개월전 + 지금은) | `19e673e` | 로컬 유닛(narrate False→True) · ⏳ VM E2E |

docs(retro): `ddef2cd 15a15e1 2160845 6602ebd 994c471 8bdcf7c 8b36b5d` (§4.5·§4.9·§4.10).
※ 사이에 board executor의 `fix(comments)` 커밋들(bdef967·ff0505f·c64ae2f·7356de2·87faf02 = 댓글답변 에이전트) 인터리브 —
board↔CLI 공용 루프, 내 RF 작업과 무관하게 공존. 현재 HEAD=87faf02(board).

---

## 관통 교훈 (retrospective에 영속)

1. **선호는 *공급* 쪽에서 bias, *거부* 쪽에서 집행 마라.** both-pet을 Giri가 솔로 반려하게 했더니 "맞는 거부+대체물
   부재=빈 슬롯"이 "틀린 러버스탬프"보다 나빴다. floor(공급)=ON, 요구게이트=OFF. (§4.10)
2. **희소자원 제외의 *단위*(파일 vs 트림)·*출처*(시도 vs 방영)를 검수기와 일치시켜라.** 선택이 검수보다 과격하면
   통과 가능한 재료를 스스로 버리고(whole-file이 segment-aware 검수기보다 과격), 시도를 "사용"으로 세면 실패가
   풀을 잠식한다(grind 자기오염). PD: "트림 단위로", "결제사후 대사(uploaded=1)". (§4.9 C_trimlevelreconcile)
3. **규칙은 '쓰는 프롬프트' 아니라 '실제 타는 경로'에 넣고, LLM이 못 맞추는 소프트 규칙은 결정론 삽입으로 집행.**
   시점마커가 concept엔 있고 singlepass엔 없었다(실행경로). era-mix는 금지 말고 **라벨해 합법화**(auto-label). (§4.9)
4. **stale 생산풀은 콘텐츠 문제로 위장한다.** "왜 자꾸 과거 memory-lane/era-mix" 상류가 실은 iCloud 전파 freeze(신선이
   안 와서 Writer가 과거로 쏠림)였다. 생성 품질만 보지 말고 데이터 freshness부터. (§4.5 C_ingestexportfreeze)
5. **"Giri가 잘 거부함" ≠ 해결.** 검수기가 올바르게 거부하면 그건 생성기가 결함을 뱉는다는 뜻 → 생성기 근본을
   고쳐 ship시켜라(빈 슬롯은 올바른 거부여도 실패). PD 교정.

---

## 미완 / 다음 세션 (resume 포인터)

- **★VM E2E 검증 (SSH rate-limit 회복 후):** 이번 세션 SSH를 과도호출해 IAP 255 rate-limit에 걸림(메모리
  `vm_ssh_controlmaster`: ControlMaster 멀티플렉싱+빠른재시도 금지). 회복 후 `launch_selfheal --date 2026-11-25`(및 26)
  재실행해 **① era auto-label이 11/25 18:00 grind를 깨고 ship하는지 ② 번 캡션 author가 '아기 레오 시절' 라벨을
  보존하는지**(안 되면 cameraman 번-side 백스톱 추가) ③ 트림-레벨로 실제 충전되는지 확인. 정규 03:00 배치가 자연히
  exercise하므로 다음날 라이브 스케줄(`agents.reconcile.list_scheduled_videos`)로도 검증 가능.
- **11/25·11/26 빈 슬롯:** 이전 수동 E2E 반복이 "최근 사용"창을 부풀렸으나 uploaded=1 대사로 자동 롤백됨. 11/25
  18:00·11/26 일부는 여전히 비어있을 수 있음(수동 백필 or 정규배치 대기). 11/22 21:00 both-pet(bO_2sVIeuPQ)·11/26
  18:00(wUviVlJ_6Q0) 등 E2E 산출물은 공개 예약됨.
- **era auto-label 다듬기(여유시):** 번-side persistence 확인 후 필요하면 cameraman 번 chokepoint에도 결정론 라벨
  재적용(캡션 author가 덮어쓰면). `지금은 지금` 같은 중복 lead-word는 드물지만 refine 여지.
- **board 댓글 에이전트:** board가 이번에 viewer-comment 답변 에이전트를 cron 배선(2×/day, Slack 승인큐). 내 작업과
  독립 — board 쪽 핸드오프 참조.

## 운영 메모
- 배포 = `git push origin main`(VM deploy.timer 2분 폴). 현재 전 fix 배포됨.
- killswitch: `RF_BOTHPET_FLOOR/ENFORCE·GIRI_BOTHPET_GATE·RF_FRESH_BOTHPET_FLOOR·RF_TRIM_LEVEL_COOLDOWN·
  RF_REUSE_MIN_WINDOW·RF_ERA_AUTOLABEL·RF_ERA_MAX_TRIES·INGEST_EXPORT_TIMEOUT` — 전부 코드 기본값 ON/적용.
- iCloud 싱크 수동 복구: Mac `python -m scripts.ingest_register --export` → VM `run_job.sh scripts/ingest_register.py --import`.
- SSH: `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap` —
  ★잦은 호출 금지(IAP 255). base64로 /tmp 스크립트 심고 `sudo -u rianileo`로 실행, 긴 작업은 nohup 독립.
