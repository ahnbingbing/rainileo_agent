# Session handoff — 2026-11-26 · GPS 장소 그라운딩 (RF "첫 바닷가 수영" 날조 근본수정)

**한 줄:** 어제 21:00 공개된 RF 쇼츠가 **내륙 하천 클립을 "겁 없는 꼬마 랴니, 첫 바닷가 수영 도전"**으로 발행(개울→바다·"첫" 단정)한 걸 PD가 잡았고, 근본이 **소스 GPS를 입구에서 버리고 VLM 픽셀추측("바닷가")을 신뢰**한 것임을 추적 → **맵 그라운딩(agents/geo.py) #1·#2 + 생성기/Giri/프롬프트 3a까지 전부 커밋·배포·검증**. GPS 갭(PD 지적)도 백필 필터 수정으로 2,025개 복구(grounded 77%). 남은 5,786개(진짜 GPS 없음)는 **이미지 클러스터링(3b)**으로 대표만 PD께 물어 전파 — 클러스터링 작동 입증했으나 **naive CLIP이 장소 아닌 펫을 임베딩**하는 걸 발견, 다음은 **장소 중심 임베딩(Places365)** 교체. 이전 핸드오프 = `notes/session_handoff_2026-11-24_mv_giri.md`.

---

## 지금 상태 (내 geo 작업 전부 커밋·푸시·배포 완료)

커밋(main, VM deploy.timer가 pull→smoke→봇재기동; MV 커밋들은 board/타 세션 것):
| 커밋 | 내용 |
|---|---|
| `4875067` | #1·#2 GPS 그라운딩 인프라 (`agents/geo.py`, Apple 오프라인 역지오코딩) + 인입배선 + 백필 |
| `e01ce15` | 회고 C_gpsground |
| `6b3ae1b` | **3a** 생성기 결정론 스크럽 (`_ground_truth_sc` chokepoint) |
| `3a11781` | **3a** Giri `_geo_ocean_claim_gate` + 프롬프트 3곳 geo마커·첫가드 |
| `e5a5287` | 회고 C_gpsground에 3a 반영 |
| `5f2957c` | GPS 갭 수정 — 백필 필터 `gps_lat`→`geo_water` (2,025 복구) |
| `0622f54` | **3b** 이미지 클러스터링 PoC (`scripts/cluster_ungrounded_geo.py`) |

- **그라운딩 상태**: `geo_water` 채워진 자산 **19,402/25,188 (77%)** (ocean 5 / inland_water 317 / none 19,080). 진짜 GPS 없는 건 **5,786개(영상 506)**.
- **VM 전파 검증 완료**: `VM_HEAD=e5a5287`, `ingest_register --import`로 25,737행 geo 반영 확인.
- **mv03 「첫눈 오는 날」**: 지난 세션 업로드가 실제 **실패**였음(manifest의 `WIITFUkf8pY`는 YT에 없음) → 재업로드 **`hzEqazO8las`**, 11/25 09:00 KST 예약(검증완료). 수동 업로드라 orphan.

---

## ★다음 세션 메인 (3b 완성 — 장소 중심 임베딩)

**naive full-frame CLIP은 장소가 아니라 피사체(펫)를 임베딩한다** — 느슨한 threshold에서 실내·잔디·대야가 다 섞인 246-blob 생성(공통점=검은 랴니가 큼). 타이트하면 같은 계곡·같은 테라스는 정확히 묶지만 질문이 많아짐(0.25서 80%커버에 83질문). threshold 스윕으로 **<10%는 수학적으로 성립**(0.40서 상위5개=80%·13개=90%)이나 그 느슨함이 장소를 섞음 → **피처를 장소 중심으로 바꿔야 coherent한 <10%**가 된다.

1. **임베딩 교체**: `scripts/cluster_ungrounded_geo.py`의 `_embed`를 **Places365**(장면인식 ResNet, torch 이미 설치됨) 또는 **배경 크롭 CLIP**(중앙 피사체 제외)로. 같은 장소 병합·다른 장소 분리 검증(몽타주 eyeball).
2. **대표 → PD 질문 → 전파**: 클러스터별 medoid만 **PD께** 질문("이 묶음 어디예요? 집/카페/테라스/공원/계곡/바다…", 프레임 포함). ★**PD만 답 가능**(함미하비 아님 — 그 자리 주인만 앎). 답을 클러스터 전체에 **전파** → `geo_place`/장소 채움 → 3a 기계가 자동 사용. (가장 큰 클러스터부터 = 적은 질문으로 최대 커버.)
3. **durable**: ingest 때 썸네일 1장 저장(향후 클러스터링 즉시) + **recurring 백필**(Mac-side; Apple이 역지오코딩을 비동기로 계속 채우니 주기 실행으로 갭 축소).
4. (#4 보류) "첫" 일반 단정어(물 외) Giri 캡 — 3b 후.

실행 메모: `python -m scripts.cluster_ungrounded_geo --kind video --threshold 0.38` (프레임·파일 로컬 캐시됨, GCS fetch 자동). 임베딩 캐시 `/tmp/geo_emb.npy`.

---

## ★이번 세션이 지은 것 (근본의 전모)

### 1. GPS 장소 그라운딩 = `agents/geo.py` (신규)
osxphotos `PhotoInfo.place`가 **Apple 오프라인 역지오코딩**(네트워크0)을 제공 — 실지명 + 구조화 수역분류 `place.names.ocean/inland_water/body_of_water`. `geo_from_photoinfo()→{lat,lon,place,water}`, `altitude_from_file()`(영상 ISO6709), `ensure_geo_cols()`, `geo_label()`. assets 신규컬럼 `gps_alt/geo_place/geo_water`. **Apple은 `ocean`을 실해안에서만 채워** "바다 여부"가 거의 결정론(문제 3클립 양양/용인/문경 전부 `none`, 진짜 ocean 5개는 터키 보스포루스 등 실해안 — 오분류 0).

### 2. 배선 + 백필
`icloud/sync.py`(신규 인입 자동 그라운딩) + `scripts/ingest_register.py`(VM import시 `ensure_geo_cols`로 Mac→VM 전파) + `scripts/backfill_geo.py`(기존풀 백필). ★**백필 필터는 `geo_water IS NULL`**이어야 함(`gps_lat` 아님) — Apple이 역지오코딩을 **비동기**로 하므로 1차 패스가 좌표만 캡처·지명은 나중에 도착, `gps_lat` 필터면 좌표-있는 행을 영영 스킵(2,025개가 그렇게 묻혀 있었다).

### 3. 3a — 생성기 결정론 그라운딩 + Giri + 프롬프트
- `_ground_truth_sc`(producer.py `_gather_context`, **singlepass+v2+AV 전 경로가 쓰는 chokepoint**): geo가 바다 반박(geo_water∈none/inland_water)인데 VLM scene에 바다/파도/바닷물 있으면 토큰 스크럽+`[지오: 지명·바다 아님(맵)]` 프리픽스 → Writer가 환각을 상속 불가.
- Giri `_geo_ocean_claim_gate`(reviewer.py): 바다 캡션·제목인데 쓴 클립 geo_water∈none/inland_water면 cap≤5·수정필요. ★**NULL geo_water는 미발화**(unknown≠not-ocean, GPS없는 자산 오탐보호). 실제 문제 컨셉서 5/수정필요 검증.
- 프롬프트 3곳(singlepass·writer_realfootage·concept): `[지오]` 마커 obey + "첫=사실주장, 랴니 물엔 항상 거짓"(랴니=평생 수영선수 canon). concept의 "첫 수영 훅 ↔ 첫 날조금지" 모순 해소.

### 4. 3b — 이미지 클러스터링 (`scripts/cluster_ungrounded_geo.py`, 신규)
CLIP(open_clip ViT-B-32, venv 설치) 임베딩 + agglomerative cosine. 대표프레임 1장/클립(GCS fetch 자동, 로컬영속) → 클러스터 → 몽타주. **작동 입증**: 같은 계곡·같은 테라스 정확히 묶음. **한계 발견**: full-frame CLIP이 펫 지배 → 장소 교체 필요(위 다음세션).

---

## 관통 교훈

1. **파일에 이미 있는 authoritative 신호(GPS/맵)를 입구에서 버리고 픽셀을 추측하면, 생성기는 '근거 없음'이 아니라 '틀린 근거에 충실'해진다 — 더 고약하다.** VLM이 scene을 "바닷가"로 환각 → Writer가 충실히 상속 → "첫 바닷가 수영". 맵이 그 환각을 override한다. (회고 §4.4 C_gpsground; [[C_grounding]]의 "GPS는 대안 아님"을 범위 한정 supersede — 그건 Slack-EXIF-stripped 자산 얘기고 iCloud/osxphotos 풀은 GPS+Apple지오코딩 보존해 69%→77% 커버.)
2. **"31% GPS 없음"처럼 비정상 수치는 데이터 파이프라인부터 의심하라(PD 직감).** 절반은 백필 필터버그(비동기 역지오코딩), 절반은 진짜 없음(공유사진·스트립·위치off — iCloud 원본 다운로드로 EXIF 직접 확인해 판별). 추측 말고 원본을 받아 쟀다.
3. **"첫/처음/최초"는 단정이 아니라 사실 주장 — 검증될 때만.** PD 반복 피드백. brittle 키워드 게이트(첫 수영/첫 풍덩)는 Writer가 단어만 비켜("첫 바닷가") 우회하니, 생성기(프롬프트)+검수기(Giri) 둘 다 고쳐야 수렴.
4. **병행 가능한 업무는 병행 제안으로**(PD 요청, 메모리 [[propose_parallel_work]]) — Giri 백스톱(reviewer.py)과 프롬프트(.md)는 독립파일이라 서브에이전트+메인 병행.
5. **묻기 전에 증명하라.** 클러스터 품질을 몽타주로 PD께 보여준 뒤에 질문. naive CLIP 한계도 246-blob 몽타주로 드러냄 — "안 된다"가 아니라 "피처를 바꾸면 된다".

메모리: [[gps_location_grounding]], [[propose_parallel_work]]. 회고: §4.4 C_gpsground.
