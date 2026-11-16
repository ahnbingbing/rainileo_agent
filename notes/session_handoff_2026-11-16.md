# Session handoff — 2026-11-16 · RF 반복 근본수정 + 레오 입양 1주년

**한 줄:** PD "11/17 동영상 또 자주 썼던 것 반복 + iCloud 새 거 많은데 왜 안 써"로 시작 → RF 반복의
**근본 4겹을 배포 수정**하고, 레오 입양 1주년 에피를 첫날(2025-11-15) footage로 만들어 **오늘 18:00
공개 예약**까지 끝냄.

---

## ★ 다음 세션/board 즉시 TODO
- **11/17 21:00(12:00Z) RF 슬롯이 비어있다 — 채워라.** 사유: 하네스(m0miu, 과다노출) veto +
  레오 1주년을 11/17 21:00 → 11/16 18:00으로 땡겨옴. 자동배치는 지난 날짜(11/17) 재방문 안 하니 수동:
  ```
  deploy/run_job.sh -m agents.launch_selfheal --date 2026-11-17 --lane real_footage --slot 21:00 --rounds 1
  ```
  신선 RF, **하네스/반복 테마 금지**. (렌더 실패 시 아래 "렌더 주의" 참고.)

## 현재 예약 상태 (확인됨)
| 날짜·시간(KST) | video_id | 내용 |
|---|---|---|
| 11/16 18:00 | Qc2nSKoRxNw | ★레오 입양 1주년 (첫날 footage, 밝기다운, walking+소파) |
| 11/16 21:00 | zJgl4nS-eKU | 스크래처 레오 |
| 11/17 18:00 | To-lJUh5vTo | 알밤 탐험 |
| **11/17 21:00** | **— 비어있음** | **← 채워야 함** |
| 11/18 18:00 | Uvg2S6BXTeA | 보라색 모자 아기 레오+랴니 (★우비 footage 자동사용 = fix 작동) |
| 11/18 21:00 | HhAOkVZauy4 | 소파 꿀잠 |

## ✅ 배포된 근본 수정 (RF 반복 — PD "자주 썼던 것 반복")
1. **신선-우선 클립 랭킹** (`9ae9de1`, producer `_hook_score` RF_RECENCY_BONUS) — 지배신호 pd_notes가
   슬랙 전용이라 iCloud footage가 구조적으로 밀리던 편향을 소스중립 신선도 보너스로 교정. 시뮬: 11/13
   클립 랭크 69→19.
2. **예약 검증 + 리콘사일러** (`9ae9de1`, upload.ensure_scheduled + reconcile.reschedule_stuck) —
   예약이 YouTube에 안 박혀 슬롯이 조용히 죽던 것(11/17 18:00 유실) 방지. 실전: veto/예약 재시도가 실제
   1차 실패→재시도 성공으로 검증됨.
3. **마일스톤 강제** (`4ebfc89`, arc `_milestone_directive`) — 기념일/생일을 "선택적 양념"이 아니라
   그날 **강제** directive로. (ARC off여도 발동, PD /concept 아래.)
4. **마일스톤 era footage 풀 주입** (`702aabb`, producer) — 강제해도 archive가 연8개 recency순이라
   입양첫날(2025-11-15)을 안 보여줘 cast 0이던 것 → 마일스톤 ±7일·해당pet·과거연도 footage 풀 주입.
   검증: 11/17 타깃에 2025-11-15 레오 15개 surfacing.
5. **캡션 할루시네이션 수정** (`2a4b0a1`, cameraman `_RF_ACTION_SYS` 프롭블록) — "소파에 비비적"처럼
   작가가 상상한 **상호작용**을 프롭-이름-보존 규칙이 복사하던 것. 이제 소품 '이름'만 참고, 상호작용은
   프레임/액션타임라인에 보일 때만. 부분프레이밍 특히 주의.

## 🧠 PD 피드백 → 아직 미구현 (다음 설계)
- **반복 ≠ footage, = 내용/느낌.** 유사 화면이라도 내용 다양하면 OK. 지금 재탕/구간중복/구조 게이트가
  과해서 한 세션으로 내용 다른 에피를 못 만들고 render시 컷드롭→"too short". **Phase 1 = 편집-시그니처
  로테이션**(프레이밍/속도/전환/캡션보이스)으로 "같은 느낌 방지"하되 차단 완화. **Phase 2 = fal.ai
  v2v** 레스타일(퀄리티 기준, 비용 아님).
- **캡션-footage 정렬의 더 깊은 축:** 캡션을 footage에 맞추는 것 + footage/트림을 의도한 beat에 맞추는
  것 둘 다. (레오 소파-비비기는 그 클립 어느 트림에도 없었음 — 근처에 있을 뿐. 작가가 소파 맥락으로 날조.)

## ⚠️ 렌더/운영 주의 (이번에 덴 것)
- **제목/썸네일 등 메타는 즉시 API 변경**(재렌더 불필요). **자막은 픽셀 번인**이라 바꿀 때마다
  워크디렉토리(`data/tmp/cameraman_<card>_<ts>/`) `animated_captioned` 재-burn + `assemble_episode`
  재조립 필요 → 느림. **자막 수정은 한 번에 모아서.**
- **`reupload_episode`는 delete=True** — 반복 호출하면 이전 영상 삭제 + 중간 업로드 유실(이번에 3개
  video_id 날아감). 새 빌드 교체는 `upload_short` 직접 + 기존 veto로.
- **긴 빌드는 nohup detached 필수** — gcloud ssh 끊기면 non-detached 프로세스 죽음.
- **로컬 scp 다운로드가 truncate**되면 "moov atom not found"(맥 QuickTime 호환 오류로 보임) — 원본은
  멀쩡, 재다운로드하면 됨.

cf 메모리: [[rf_editing_direction_memories]] · retrospective C_sourceblindhook/D_scheduleverify/C_milestoneforce.
