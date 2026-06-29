# 작업2 설계 — `icloud/sync.py` 분리 (Mac 입구 / VM 등록)

> [gcp_migration_plan.md](gcp_migration_plan.md) §7 작업2의 상세 설계. **코드 전 PD 합의용.**
> 핵심: osxphotos(Mac 전용)를 인입에서 떼어, Mac은 pull+GCS+매니페스트만, VM은 매니페스트→DB.

> **★ PD 2026-06-28 단순화 (실측 후):** DB 자산의 **95.4%(15,833/16,602)가 이미 GCS에 있음.**
> → 벌크/렌더 경로는 **GCS-pull only**(분리 불필요, 아래 4모듈은 과설계였음). 남은 실작업은
> 셋으로 축소: ① **769(실제 735) 누락분 1회 백필**(`scripts/gcs_backfill.py`, launchd로 실행 —
> osxphotos TCC 권한은 launchd 부모일 때만, 진행중) ② **VM 렌더=GCS-only**(cameraman osxphotos
> 폴백 `_osxphotos_available()` 가드) ③ **인입 시 GCS 보장**(Slack=작업1 완료, 태깅 세이프티 한 줄).
> osxphotos는 **새벽 1회 얇은 델타**(Slack 안 거친 신규)로만 잔존 — 그 델타에만 아래 매니페스트
> 핸드오프가 필요(벌크엔 불필요). 즉 §2 4모듈은 "델타 전용·축소판"으로만 의미.

## 0. 현 구조 (1401줄, 함수 25개)

| 묶음 | 함수 | 이식성 |
|---|---|---|
| **osxphotos pull** | `_osxphotos_lock` `bulk_export_to` `_osxphotos_cli` `_osxphotos_healthy` `download_asset_by_uuid` `download_assets_by_uuids` `backfill_uuids` `_resolve_local_source` | ❌ Mac |
| **DB 등록** | `insert_asset` `asset_exists` `ensure_source_uuid_column` | ✅ VM |
| **VLM** | `_run_vlm_tagging` | ✅ VM (Gemini API) |
| **순수 헬퍼** | `_short_hash` `_asset_id` `_year_dir` `infer_age_tag` `map_subjects` `compute_phash` `parse_subject_map` | ✅ 공용 |
| **디스크 관리** | `prune_originals` `warm_working_set` `_pending_card_asset_ids` | △ 로컬개념→VM은 GCS-fetch |
| **오케스트레이터** | `sync_album` `main` `print_summary` | 분해 대상 |

## 1. 제일 중요한 발견 — 렌더 경로 지뢰 (이미 절반 안전)

`agents/cameraman.py`가 렌더 시점에 `icloud.sync`의 osxphotos 재다운로드를 호출한다
(`download_assets_by_uuids`@7242, `download_asset_by_uuid`@3801, `_osxphotos_healthy`@7267).
**리눅스 VM에선 osxphotos가 없다.**

**다행:** 그 경로는 **이미 GCS-우선**이다 (cameraman.py:7226 *"gcs bulk prefetch failed,
falling back to osxphotos"*). 즉 GCS에서 먼저 당기고, 실패할 때만 osxphotos 폴백.
그리고 *"whatever still doesn't arrive is left for the per-cut gate to swap/drop; render
never downloads"* — GCS 미스는 이미 per-cut 게이트가 처리.

**→ VM에선 osxphotos 폴백만 막으면 끝.** 큰 리팩터 불필요. GCS 미러가 이제 포괄적이라
(osxphotos 인입 + Slack 인입[작업1] 둘 다 GCS 푸시) 폴백은 거의 안 fire.

**단 하나 제약:** osxphotos를 **VM이 import하는 어떤 모듈의 top-level에서도 import하면 안 된다**
(그 한 줄이 VM의 모든 import를 깨뜨림). osxphotos 접근은 subprocess-CLI거나 `ingest_local`에 격리.

## 2. 모듈 레이아웃

```
icloud/
  sync_common.py   (NEW, 공용·이식)  순수 헬퍼 + Asset/Manifest 자료형
                    _asset_id _year_dir infer_age_tag map_subjects compute_phash
                    parse_subject_map _short_hash. osxphotos·DB 의존 0.
  ingest_local.py  (NEW, Mac 전용)   osxphotos pull(bulk_export_to/download_*/backfill/
                    _osxphotos_*/_lock/_resolve_local_source) → sync_common으로 메타계산
                    → gcs.upload → 매니페스트 emit(gs://…/manifests/). 로컬 prune. DB 0.
  ingest_register.py (NEW, VM)       매니페스트 read(GCS) → 파일 보장(gcs.download_to) →
                    insert_asset → _run_vlm_tagging. DB 소유. asset_exists/ensure_*_column 이동.
  fetch.py         (NEW, 공용)       ensure_local(asset): GCS download_to → (Mac만) osxphotos
                    uuid 폴백. _osxphotos_available()로 가드(리눅스=False). 렌더가 직접
                    osxphotos 대신 이걸 호출.
  sync.py          (KEPT, 호환 shim)  이동한 이름 re-export → 기존 import(cameraman/scripts)
                    마이그레이션 중 안 깨짐. osxphotos top-import 금지. 종국엔 thin.
```

## 3. 매니페스트 스키마 (Mac→VM 핸드오프)

`insert_asset` 컬럼 중 *Photos.app만 아는 것*만 Mac이 실어 보낸다. 파일 있는 메타
(w/h/dur/phash)는 Mac이 미리 계산(파일 보유 = 더 쌈).

```json
{
  "generated_at": "2026-06-28T01:30:00+09:00",
  "album": "Ryani & Leo",
  "assets": [{
    "asset_id": "med_2026_06_28_..._icloud_ab12cd34",
    "source": "icloud",
    "kind": "photo",                       // photo|video
    "blob_name": "photos/2026/med_....jpg", // = file_path 상대(GCS 객체명)
    "captured_iso": "2026-06-28T10:22:01",  // Photos 메타 (Mac만 앎)
    "subjects_csv": "leo,ryani",            // Photos People&Pets→map_subjects (Mac만)
    "age_tag": "mixed",                     // infer_age_tag(subjects, captured)
    "source_uuid": "....",                  // Photos UUID (재다운로드 키, Mac만)
    "duration_sec": null, "width": 4032, "height": 3024, "phash": "...."
  }]
}
```
VM의 `ingest_register`: 각 asset → `gcs.download_to`(필요시) → `insert_asset(**asset)` →
`_run_vlm_tagging`. vis_phash(지각해시)는 별도 VM 잡(`agents/visual_hash.py`) 그대로.

## 4. 호출부 영향 (blast radius)

| 호출부 | 현재 | 변경 |
|---|---|---|
| `agents/cameraman.py` ×3 (렌더, AV+RF 공통) | `from icloud.sync import download_assets_by_uuids/_osxphotos_healthy` | **`fetch.ensure_local`로 교체** + osxphotos 폴백 `_osxphotos_available()` 가드. VM 핵심 |
| `scripts/retag_subjects.py` | `from icloud.sync import (…)` | shim로 무변경 가능, 추후 sync_common 리포인트 |
| `scripts/gcs_backfill.py` | `download_assets_by_uuids` | Mac 전용 스크립트 — `ingest_local` 리포인트 |
| `launchd icloud-sync` | `python -m icloud.sync --download-missing --vlm --prune --warm` | Mac: `python -m icloud.ingest_local --prune`; VM: 신규 `ingest_register` 타이머 |

## 5. 마이그레이션 순서 (shim으로 비파괴, 각 단계 독립 검증)

1. **sync_common.py** 추출 — 순수 헬퍼 이동, sync.py가 거기서 import. 동작 무변경.
2. **ingest_register.py** — insert/register/vlm 이동, sync.py shim re-export. 로컬에서 매니페스트→DB 검증.
3. **ingest_local.py** — osxphotos pull + 매니페스트 emit. Mac에서 dry-run(매니페스트 1장 생성 확인).
4. **fetch.ensure_local** 도입 + cameraman/scripts 리포인트 + osxphotos 가드. **렌더가 Mac/VM 양쪽서 동작**하는지 검증(Mac=폴백 살아있음, 가드 False시뮬=GCS-only).
5. **launchd 교체** — Mac icloud-sync→ingest_local; VM ingest_register 타이머(프로비저닝 시).
6. **sync.py shim thin/은퇴**.

> 1~4는 **프로비저닝 전, 로컬에서 전부 가능**($0). 5만 VM 게이트.

## 6. 체크포인트 / 열린 질문

- **C1.** ✅ 해결 — `import osxphotos`는 함수 내부(라인 552·855)뿐, 모듈 top 없음. 대부분
  subprocess-CLI(`_osxphotos_cli`). → 리눅스서 `from icloud.sync import …` 안전(호출해야만 로드).
  shim VM-safe, ingest_local 격리만 지키면 됨.
- **C2.** `_run_vlm_tagging`이 로컬 파일 경로 전제? VM에선 gcs.download_to 후 태깅하도록.
- **C3.** prune/warm: Mac은 업로드 후 prune OK. VM은 warm=GCS-fetch(working set). 분리 단계엔 비포함, 별도.
- **C4.** 매니페스트 중복/재처리: ingest_register는 idempotent(INSERT OR IGNORE) — 같은 매니페스트 재소비 무해. 처리완료 매니페스트는 `gs://…/manifests/done/`로 이동.
