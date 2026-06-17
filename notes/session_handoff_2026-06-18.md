# 세션 핸드오프 — 2026-06-17 저녁 → 06-18 (마라톤)

> 브랜치: `approach-d-grounded-singlepass`. 모든 작업 커밋됨(b394910 → 7a8c48f).
> 한 줄: **RF 근본 2건(나들이 primitive·가로 레터박스) + AV prefetch retry + grandmompapa
> 에셋 싱크 구축. 그리고 03:00 launch를 죽이던 NameError를 발견·수정.**

---

## 0. ⚠️ 가장 중요 — 03:00 launch가 크래시하고 있었다 (수정함)
- **증상(PD: "왜 3시 launch가 안 돌았어?")**: launch는 *돌았다*(6/19 카드 14개 생성, launchctl exit=0).
  그러나 **모든 렌더가 크래시** → 6/19는 4슬롯 중 1개만 공개됨(AV 1), 나머지는 approved인 채 미렌더.
- **원인**: `cameraman.py:generate_manifests`의 단일-사진-플래시 드롭 블록이 `if progress_cb:`를
  호출하는데 이 함수엔 `progress_cb` 파라미터가 **없다** → `NameError`. 사진 컷을 드롭하는 컨셉마다
  렌더 전체가 죽음. 커밋 `bd74bcc`(6/16)가 넣은 잠복 버그.
- **수정**: `7a8c48f` — 그 호출을 `log.info`로 교체(모듈 logger, 항상 스코프 내). 이제 안전.
- **남은 일 — 6/19 재채움 (PD 결정 대기, 2026-06-18 기준)**: 6/19는 현재 **1/4 슬롯만 공개**
  (AV 1), 나머지 12개 카드는 approved인 채 미렌더. 오늘 밤 03:00 배치는 6/20을 만들지 6/19를
  **안 고친다** → 수동 재실행 필요. 버그(`7a8c48f`)는 고쳤으니 재렌더하면 정상.
  - RF만(무료, 즉시): `python -m agents.launch --date 2026-06-19 --lane real_footage --no-upload`
  - 전체(AV 포함, ⚠️ **유료 ~$50/편**): `python -m agents.launch --date 2026-06-19`
  - 또는 6/19는 두고 6/20부터 정상화(03:00 자동).

## 1. 운영 상태 — 6/18 (확정, 겹침 없음)
| KST | 레인 | video_id | 내용 |
|---|---|---|---|
| 08:00 | RF | `EsCnWlhbWHY` | 6년 전 수영장 (레터박스, 랴니 보임) |
| 12:30 | AV | `9tqE6_VmR_U` | 레오 특공대 벌레 |
| 18:00 | AV | `YsxK2MekkPg` | 경비견과 대사냥꾼 |
| 21:00 | RF | `k05Sm38R0t8` | 집 안 낮은 테이블, 식사 앙상블 |

- PD가 03:00 배치(버그 전, 구버전)가 만든 '각자의 방식' RF 2편을 veto: `i_L73PG1eKE`(삭제),
  `He-gv_coYxM`(private). ⚠️ **He-gv는 private인데 publishAt이 남아있을 수 있음** — 자동모드
  가드레일로 내가 못 지웠으니 PD가 Studio에서 18:00에 안 뜨는지 확인 필요.

## 2. 이번 세션 커밋 (무엇/왜)
| commit | 내용 |
|---|---|
| `b394910` | **AV prefetch 재시도 복원** — `icloud/sync.py:download_assets_by_uuids`. 일괄-fix(d67c980)가 빠뜨린 transient 0/N retry 복원(누락분만 재export, backoff, budget 내). 자동 AV 빈슬롯 직접원인. |
| `be0b941` | **RF 나들이 primitive** — `_rf_event_clusters`로 같은날+장소 클립을 candidate_outings로 묶어 Writer에 제시("ONE 나들이로 video-first"). realfootage_concept.md STEP1/2 정합화(흩뿌리기 권장 삭제). |
| `213e7b7` | **나들이-단위 신선도 + event-worthiness 랭킹** — 풍부한 안-써본 outing은 장소 과대표집이어도 cap 면제(반복단위=이벤트). 진짜 외출(카페/엄마집) > home blob(richness≠사건). |
| `0b97639` | **RF 가로 레터박스** — landscape 클립을 crop 대신 레터박스(폭맞춤+위아래 블랙). crop이 랴니를 잘라 "안 보이는 샷" 유발. `RF_LANDSCAPE_MODE=letterbox`(기본). 충돌하던 prominence-드롭 강화는 되돌림. |
| `c7e01db` | **grandmompapa 채널 싱크** — slack_sync에 추가(#photos와 동일 미디어→assets). `SLACK_GRANDMOMPAPA_CHANNEL=C0BASN221UL`. 누가 올리든 인입. |
| `0ac7195` | **Slack 미디어 촬영일 스탬프 + 백필** — `extract_captured_at`(영상=ffprobe creation_time, 이미지=EXIF). `med_<date>_slack_<hash>`+captured_iso로 날짜기반 나들이 클러스터 편입. `--backfill-dates`. |
| `7a8c48f` | **(위 §0) launch-killing NameError 수정.** |
| (PD/병렬세션) `2584376` AV 대수술, `8e5f694` grandmompapa 대화형 에이전트 — 내가 만든 거 아님. |

## 3. RF 품질 진행 (검증 방식)
각 변경을 6/18 RF dry-run 전후 비교로 검증(PD 메타규칙 "각 변경이 실제로 돕는지"):
베이스라인 5컷/4일 잡탕('각자의 방식', calm, 실패) → +클러스터 2컷/1일(빈약) → +나들이신선도
7컷/1일(dense but eventless home) → +event랭킹 4컷 한 엄마집 나들이(warm, reviewer 통과).
실렌더(수영장 레터박스)는 Giri 9/10.

## 4. 미해결 / 다음
1. **6/19 재채움** (§0) — 버그 고쳤으니 재렌더. AV 유료 주의.
2. **2편째 RF 풀 고갈** — fresh event-worthy 나들이가 적으면 macro reviewer가 재탕 판정 → 2번째
   슬롯 실패(junk 대신 빈슬롯). 다음 진짜 레버.
3. **He-gv 잔여 publishAt** — PD Studio 확인.
4. **Slack 이미지 EXIF 스트립** — 영상은 촬영일 OK, 사진은 업로드시각 폴백될 수 있음.
5. **GCS 마이그레이션** — 설계만(docs/gcs-migration.md), 미구현. 급하지 않음.
6. **18:00 슬롯 더블/Latin square** — 이번엔 수동으로 해소했지만 배치 스케줄러가 가끔 같은 슬롯에
   둘을 잡는 원인은 미규명.

## 5. 빠른 참조 (코드)
- 나들이: `agents/producer.py:_rf_event_clusters` + `_propose_realfootage_singlepass`(candidate_outings/_protect_ids)
- 신선도: `_lead_with_underused`/`_cap_overused_locations`(protect_ids), `_recent_la_usage`
- 레터박스: `agents/cameraman.py:_letterbox_fill_filter` + RF 트림 리프레이밍(`RF_LANDSCAPE_MODE`)
- 주인공-부재 게이트(원복): `cameraman.py:_rf_caption_grounding_gate`(pet_absent_tags, all-absent)
- Slack 싱크: `scripts/slack_sync.py`(CHANNEL_ROUTES, extract_captured_at, --backfill-dates)
- prefetch: `icloud/sync.py:download_assets_by_uuids`
- launch 버그자리: `cameraman.py:generate_manifests`(progress_cb 스코프 주의)

메모리: [[rf_outing_primitive]] [[rf_landscape_letterbox]] [[slack_sync_pipeline]]
[[session_handoff_0617_dawn]]
