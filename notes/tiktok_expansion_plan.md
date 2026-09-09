# TikTok 확장 계획 (YouTube + TikTok 동시 발행)

> **상태:** 설계 확정, 구현 대기. PD 요청으로 **이번 주말에 진행.**
> 작성 2026-09-08 (CLI 세션). 결정은 아래 "확정된 결정" 참조 — 재논의 불필요, 바로 구현 시작.

## 왜 (한 줄)
유튜브 단일 → 유튜브 + 틱톡 동시 발행. 렌더/컷은 재사용, **마지막 burn+assemble만 틱톡용으로 한 번 더** ($0 ffmpeg). 유튜브 경로는 무손상.

## ★ 상위 전략 분기 (PD 2026-09-08)
- **틱톡 = 숏츠 유지**, 단 `impact_edit.py`의 임팩트 교차편집 문법(velocity/meme/story-페이오프선공개)으로 민다. 상세 `impact_edit_plan.md`.
- **유튜브 = 롱폼으로 이행**(story 페이오프-선공개 문법이 씨앗). 같은 소재를 틱톡=임팩트 숏츠 / 유튜브=롱폼으로 갈래.
- ∴ 이 문서의 "번인 세이프존 재배치·packaging arm"은 그 틱톡 숏츠 파이프의 발행단이고, 편집 문법은 impact_edit 트랙에서 온다.

## 확정된 결정 (PD 2026-09-08)
1. **발행 방식 = Phase 1 인박스(드래프트) 모드.** 완성 mp4 + 틱톡 캡션을 PD 틱톡 드래프트함에 자동 push → PD 원탭 발행. 심사 불필요. (수동 재업로드 X, 스케줄러 구독 X.)
2. **캡션 = 둘 다 손봄.** (a) 포스트 설명/해시태그를 틱톡 톤으로, (b) 화면 번인 자막을 틱톡 세이프존으로 재배치.
3. **범위 = 하루 4편(AV2+RF2) 전부** 틱톡에 병행 발행.

## 왜 인박스 모드인가 (핵심 정책 근거)
TikTok Content Posting API 2026 현재:
- 미심사 앱은 **Direct Post(직접 공개발행) = 무조건 비공개(SELF_ONLY)** — 공개 자동발행 불가.
- **Upload(인박스/드래프트) 모드는 심사 없이 됨** — PD 드래프트함에 꽂고 PD가 발행 시 공개 선택.
- 미심사 제한 "24h 5유저"는 PD 1명이라 무의미.
- ∴ "수동 재업로드 vs 심사 대기" 둘 다 안 골라도 됨.
- 스케줄러(Buffer 등)가 되는 이유 = 회사가 심사를 **한 번** 통과해 전 유저 서비스. 심사는 앱 단위지 포스트 단위 아님. 우리가 자체 앱 쓰면 우리가 직접 심사받아야 함(느리고 불확실, 수 주).
- 출처: vorplabs.com/agent-tools/tiktok-content-posting-api · postpeer.dev/blog/best-tiktok-posting-api

## 전체 그림
```
[기존] 렌더 → burn(유튜브 세이프존) → assemble → YouTube 예약공개
                                              ↓ (신규 분기)
[신규]                   → burn(틱톡 세이프존) → assemble → TikTok 인박스 push
                            + 틱톡 packaging arm(훅+해시태그)  → Slack "원탭 발행 준비됨" 알림
```

## 작업 분해 (의존성 순)

### A. 크레덴셜 불필요 — 주말에 먼저 착수
1. **틱톡 세이프존 re-burn** — `scripts/burn_captions.py`.
   - 현재 좌표: 화면 바닥 기준 **KO 220px / EN 100px** (하단 5~11%). 상수 `scripts/burn_captions.py:254-255`, y식 `:390` (`y=h-text_h-{y_from_bottom}`). 화면 1080×1920.
   - 추가: `--safe-zone {youtube|tiktok}` 플래그. tiktok = 자막을 **중앙~상단**으로(하단 ~20% + 우측 ~12% UI 회피). 유튜브 기본값은 그대로, 플래그로만 분기.
   - 시연: 기존 렌더된 에피소드 하나로 유튜브 vs 틱톡 자막위치 비교 프레임 뽑아 PD 확인받기.
2. **틱톡 packaging arm** — `agents/channel_manager.py:make_packaging` (`:174-192`, arm 3종 hook_search/hook_strong/search_strong 회전).
   - 추가: `tiktok` arm — 짧은 훅 + 트렌드 해시태그(`#fyp #catsoftiktok #frenchie #dogsoftiktok`), 설명 길이 짧게.
   - 번인 캡션을 ground truth로 쓰는 기존 정합 가드(`actual_captions_for_video`, title-caption 정합 `producer.py:4672-4686`) 상속.

### B. PD 크레덴셜 필요 — 셋업 후
3. `tiktok/oauth.py` — `youtube/oauth.py`(`:102 get_youtube()`, token `:45`) 미러. 토큰 저장/자동갱신, `tiktok/token.json`.
4. `tiktok/upload.py` — `upload_to_inbox()`. Content Posting API **Upload(FILE_UPLOAD)** 사용 (PULL_FROM_URL은 도메인 인증 필요 → 회피). 반환 `publish_id`.
5. **배선** — `agents/producer.py:auto_upload_episode` (`:4785-4843`, 유튜브 `upload_short` 호출부). 유튜브 성공 뒤 틱톡 인박스 push + Slack 알림. 카드에 `tiktok_publish_id`/`tiktok_status` 컬럼 추가.
   - ⚠️ **pipeline-change-impact**: veto 경로(`youtube/upload.py:veto_video`)도 틱톡 대응 고려 — 인박스 드래프트는 아직 미발행이라 veto = 알림 취소/드래프트 방치로 충분할 수 있음. 구현 시 재확인.
6. **Phase 2 (나중)**: 심사 제출(데모영상·개인정보처리방침 URL·URL 인증) → 통과 시 `video.publish` scope로 Direct Post 자동 공개발행 + 예약으로 승격 = 유튜브 동급.

## PD 숙제 (Phase 1 업로드 블로커 — 주말 전/중 병행)
1. developers.tiktok.com 개발자 계정 등록
2. 앱 생성 → **Content Posting API** 제품 추가 → scope **`video.upload`** (인박스용. 공개발행 `video.publish`는 Phase 2)
3. Redirect URI 등록 (CLI가 로컬 콜백 URL 제공 예정)
4. **Client Key + Client Secret** → `.env`에 `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`
→ 발급되면 CLI가 3~5 붙이고 PD 계정 OAuth 태워 인박스 테스트 1편.

## 재개 체크리스트 (주말)
- [ ] A1 세이프존 re-burn + 비교 프레임 PD 확인
- [ ] A2 틱톡 packaging arm + 독립 테스트
- [ ] PD 크레덴셜 발급 확인 (.env)
- [ ] B3 tiktok/oauth.py + OAuth 1회
- [ ] B4 upload_to_inbox 인박스 테스트 1편
- [ ] B5 producer 배선 + Slack 알림 + 카드 컬럼 (change-impact: veto/양레인 확인)
- [ ] E2E: 하루 4편 유튜브 예약 + 틱톡 드래프트 동시
```
