# GCS 마이그레이션 설계

> 목적: 로컬 노트북 디스크 압박(미디어 자산 ~20G)을 영구 해소하고, 이미 GCP(Vertex AI)를 쓰는 파이프라인의 **저장 계층도 GCP로 일원화**한다.
> 작성: 2026-06-16

## 배경 / 현재 구조

- **연산(compute)**: 이미 GCP 사용 중 — Vertex AI(`aiplatform`, gemini-*) + YouTube API. 정기 스케줄 존재(`WRITER_RUN_HHMM`, `PUBLISH_HHMM`, `launchd/`).
- **저장(storage)**: 전부 로컬. `google-cloud-storage` 의존성 없음.
  - 경로는 중앙 설정 모듈 없이 각 파일이 `ROOT = Path(__file__).resolve().parent.parent` 후 `ROOT/"data"/…` 로 흩어져 참조.
  - 일부만 env 오버라이드 존재: `DB_PATH`, `ASSET_STAGING_DIR`, `LOGS_DIR`, `ICLOUD_INBOX`.

### 현재 로컬 용량 (정리 시점)
| 데이터 | 크기 | 비고 |
|---|---|---|
| `data/assets/photos` | 5.3G | 입력 원본 사진 |
| `data/assets/clips` | 2.5G | 입력 클립 |
| `data/output/episodes` | ~11G | 완성 에피소드 (현재 iCloud로 임시 아카이브됨) |
| `data/output/*` | 나머지 | 작업중 산출물 |
| `data/agent.db` | 25M | **SQLite** |

## 무엇을 어디에 둘지

| 데이터 | 이전 대상 | 스토리지 클래스 | 이유 |
|---|---|---|---|
| `data/assets/photos` | GCS | Nearline | 입력 원본, 가끔 접근 |
| `data/assets/clips` | GCS | Nearline | 입력 클립 |
| `data/output/episodes` | GCS | Coldline / Archive | 완성 아카이브, 거의 안 봄 |
| `data/output/*` (활성) | GCS | Standard | 활성 산출물 |
| `data/agent.db` (SQLite) | **로컬 / VM 영구디스크 유지** + GCS 백업 | — | ⚠️ 아래 주의 |
| `data/logs` | Cloud Logging 또는 GCS | — | 부수적 |

> ⚠️ **SQLite는 GCS에 직접 두지 말 것.** 객체 스토리지는 부분 쓰기/파일 락을 지원하지 않아 손상 위험. 로컬 또는 VM 영구디스크에 두고 주기적으로 GCS에 백업. 규모가 커지면 Cloud SQL / Firestore로 분리 검토.

## 비용 (대략)
- 활성 입력 ~8G(Nearline ~$0.01/GB·월) + 아카이브 ~11G(Coldline ~$0.004/GB·월) ≈ **월 $0.1 미만**.
- 실질 비용 변수: **egress(재다운로드 ~$0.12/GB)** 와 VM 시간. 저장 자체는 사실상 무시 가능.
- 권장: 라이프사이클 규칙으로 `output`을 N일 후 Coldline 자동 전환.

## 코드 추상화 — 두 갈래

### A. gcsfuse 마운트 (코드 변경 최소)
- `gs://<bucket>/data` 를 `./data` 에 마운트 → 기존 `Path` 코드 거의 그대로.
- GCP VM에서 운영 시 최적. 단점: 지연시간, **SQLite/랜덤액세스 부적합**(DB는 마운트 밖 로컬 디스크에 둘 것).

### B. 스토리지 레이어 도입 (이식성↑, **권장**)
- `agents/storage.py` 신설: `get(logical_path)`, `put(...)`, `list(...)`, `exists(...)` + 로컬 캐시 디렉토리.
- 흩어진 `ROOT/"data"/assets|output` 참조를 이 레이어 경유로 교체 (DB·logs 제외).
- env 토글: `STORAGE_BACKEND=local|gcs`, `GCS_BUCKET=...`, `GCS_CACHE_DIR=...`.
- 로컬에서도 클라우드에서도 동일 코드로 동작 → 테스트/개발 용이.

**결론: B를 기본, GCP VM 운영 시 보조로 A.**

## 마이그레이션 단계

### Phase 0 — 준비
- [ ] 빌러블 GCP 프로젝트 확인 (Vertex용 기존 프로젝트 재사용 가능, `gen-lang-client-*` 불가 — `CLAUDE.md` 참고)
- [ ] 버킷 생성: `gsutil mb -l <region> -b on gs://<bucket>`
- [ ] 라이프사이클 규칙: `output/` → N일 후 Coldline, 입력 → Nearline
- [ ] 서비스 계정 + 권한(`roles/storage.objectAdmin`)

### Phase 1 — 추상화
- [ ] `agents/storage.py` 작성 (local/gcs 백엔드, 로컬 캐시)
- [ ] `requirements.txt`에 `google-cloud-storage` 추가
- [ ] `data/assets`·`data/output` 참조를 storage 레이어 경유로 교체 (DB·logs 제외)
- [ ] `.env.example`에 `STORAGE_BACKEND`, `GCS_BUCKET`, `GCS_CACHE_DIR` 추가

### Phase 2 — 데이터 이전
- [ ] `gsutil -m rsync -r data/assets gs://<bucket>/assets`
- [ ] `gsutil -m rsync -r data/output gs://<bucket>/output`
- [ ] iCloud 임시 아카이브분(episodes)도 GCS Coldline으로 일원화
- [ ] 검증(개수/체크섬) 후 로컬 삭제

### Phase 3 — (선택) 실행 이전
- [ ] 스케줄(`launchd/`)을 **Cloud Run Job + Cloud Scheduler** 또는 소형 GCE VM으로 이전
- [ ] 인증: 로컬 ADC → 서비스 계정 / Workload Identity
- [ ] DB: VM 영구디스크 또는 Cloud SQL, GCS 백업 잡 추가

## 기대 효과
- 노트북에서 **~20G 영구 제거**, 디스크 압박 재발 방지.
- 저장·연산이 GCP에 co-locate → egress 절감, 운영 단순화.

## 참고 — 이번 디스크 정리 내역 (2026-06-16)
- Downloads 중복 설치파일 삭제 (~2.1G)
- `output/episodes` 417개 중 최근 10개만 로컬 유지, 나머지 iCloud Drive로 아카이브 후 evict (로컬 11G → 0B)
- (보류) Library 캐시, `.venv`/`__pycache__`, Claude `vm_bundles`(14G, 로컬 런타임 — 별도 정리 대상)
