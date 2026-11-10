# Session handoff — 2026-11-11 (RF 편집 심화: 멀티컷·연출·캡션 그라운딩)

**스파인:** 11/10 "RF를 Apple 추억 그래머로" 작업의 연속. PD가 실제 렌더를 눈으로 보며 결함을
연이어 지적 → "통계 말고 끝까지 렌더해서 봐라, 느리면 파이프라인 문제다"가 관통 교훈. 여러 "1컷
붕괴" 근본과 캡션 액션-오독 근본을 레이어별로 찾아 고침. **미수정 1건(era-mix 시점마커) = 다음 세션.**

## ✅ 오늘 배포 (전부 가역, main)
| 커밋 | 내용 |
|---|---|
| 2aea6ac | 사진=영상 **beat별 동등 후보**(할당량 아님, PD 교정). 261800a 할당량 directive supersede |
| 0899bdb | **RF 본문 cap 18s→50s** (PD "길이는 60초 안이면 OK"). 18s cap이 멀티컷을 1~2컷으로 쪼개던 상위원인 |
| 5dc6e03 | 에디터 뒤 본문예산 결정론 재적용(sources.json disconnect로 부분적; 50s cap이라 거의 안 탐) |
| d0f6c10 | `_rf_cap_cuts` 드롭→**축소**(가독 floor까지) — [10,5,9]=24s가 cut1만 남기던 것 |
| cb54571 | **연출 팔레트 directive**: Writer가 풋티지 보고 맞는 기법 선택(나란히→split_screen·두장소→cross_cutting·전후→before_after), themed=폴백. + 사진 모든 beat에서 실제 평가(건너뛰기 금지) |
| 2221ac9 | ★**캡션 VLM이 정지3프레임→비디오(mp4)로** 읽게. 정지론 모션 못봐 그루밍/간식먹기를 "꿀잠"으로 오독했음 |
+ 11/10분: 55804ac(원테이크 prompt기본값 제거)·a7d3dbf(사진 재료검증 0.04s)·b8f4237(사진 RAG)·9b57c90(크로스디졸브).

## ★핵심 발견 (회고 C_multicutlayers·C_photozerosec 기록)
- **"1컷으로 나온다"의 범인이 여러 레이어**: ①prompt 원테이크 기본값 ②사진 재료검증 0.04s ③producer cap 드롭 ④**cameraman 에디터 LLM(`_run_editor`)이 진짜 컷 authority**=producer cap 덮어씀 ⑤18s cap 자체. → **끝까지 렌더해야 다 보였다**(컨셉 통계 통과여도 소비처(에디터·cap·sources.json)가 뒤집음).
- **캡션 액션-오독 근본 = 프레임 VLM**: `_vlm_post_render_caption_rewrite`가 0.5/2.5/4.5s 정지 3장만 봄 → 모션 못읽어 "레오 테이블 간식 몰래먹기"를 "꿀잠/그루밍"으로. **video-VLM 전환으로 수정**(테스트 통과: cut3을 "의자서 뛰어내려 테이블 것 먹음"으로 정확히 읽음).
- **"기리 통과?" 오해**: 내가 보여준 건 `_render_realfootage_direct` **직접 렌더=Giri 건너뜀**(validator 게이트 없음). 프로덕션 self-heal엔 Giri 돈다. 테스트 렌더를 결과처럼 보여준 게 혼란.
- **"한글 깨짐" = 거짓경보**: 240px 그리드 썸네일의 흐릿한 한글을 내가 오독. 실제 캡션 JSON은 멀쩡("결국 둘이 꼭 붙어 꿀잠 타임" 등).
- **렌더 9분 근본**: render_card 안에 LLM 3개(에디터·VLM검수·캡션재작성)+컷별 4K 재인코딩. "느리면 파이프라인 문제"(PD)가 가리킨 구조과제.

## 🔴 미수정 (다음 세션 — 우선순위)
1. **★era-mix 시점마커 게이트 (PD 강조, 이전에도 말한 것).** PD: "아기 레오+지금 섞어도 되는데, **그럴 땐 과거 회상씬이라든지 시간 차이를 캡션으로 말해야** 한다." 11-21 카페 에피가 cut2/3=**2025-11-21(1년 전, 어린 레오)** + cut1/4/5=2026-10~11을 **시점마커 없이 "같은 현재 카페나들이"로 봉합** → 레오가 갑자기 작아짐. 규칙은 있으나(writer_realfootage:257·267, realfootage_concept:103-110) **약함**=Writer가 years_ago를 못 알아챔. **수정=결정론 게이트**: 한 에피 컷들 촬영일 span 계산→크게 벌어지면(>~4개월/다른 해) & 메모리레인/before_after 아니면 & 캡션이 시점 안 밝히면 → "[시대혼합] 같은 시기 통일 or 과거↔현재 시점 명시"로 Writer 재작성 강제(기존 [구조검증] 패턴) + Giri 캡(lockstep). 킬스위치 필수.
2. **캡션 타이밍 살짝 늦음** (PD). 가설=내가 넣은 크로스디졸브(0.4s)가 캡션(0.2s 시작)을 디졸브 중 화면에 띄워 "늦게" 느껴짐. 디졸브 단축 or 캡션을 디졸브 완료 후 시작으로 튜닝. (`_fit_writer_caps` producer:2188, even-spacing 2416.)
3. **렌더 9분 속도** = 구조 최적화(에디터/VLM LLM 호출 줄이기 or 병렬, 4K 사전 다운스케일).

## 검증된 것
- 연출 다양성: 강제없이 Writer 3회 → cross_cutting(11-21 카페 A-B-A)·long_take(11-23)·themed+사진1장(11-25). 더는 themed만 아님.
- video-VLM: 11-21 컷들 "의자서 뛰어내려 테이블 것 먹음"·"졸린듯 눈감았다뜨고 코핥기" 정확히 읽음.
- 50s cap: 11-16 산책 6컷→6컷(32s)·[10,5,9]→3컷 보존.
- 실렌더 전달: /tmp/rf_walk_11_16.mp4(산책 4컷)·rf_crosscut_11_21.mp4(교차편집)·rf_rest_11_25.mp4(쉬는 3컷).

## 운영 메모
- 배치=**03:00 KST**(00:00 아님). deploy.timer 2분 폴.
- ssh 255 반복 = **인프라 아님**(플레인 ssh는 exit 0). `;`+sleep 다문장/백그라운드(`& disown`) 명령에서 남. 해결=단순 단일명령, 렌더는 포그라운드 run_in_background 또는 setsid.
- VM 비밀키 echo 차단 훅: 스크립트에서 `_g.Client()`(api_key 생략, env자동) 쓰면 우회.
- 직접렌더=`producer._render_realfootage_direct(concept, date, _db(), cb)` (Giri 없음, 빠른 룩확인용). run_job.sh로 감싸면 video_actions키 로드.
- ★다음세션 할일=테스트 프로세스 0 확인(pgrep dr16/dr25/dr21/launch_selfheal) 후 era-mix 게이트부터.

## 참조
- 메모리: [[rf_editing_direction_memories]]·[[grounding_pd_notes_gpt4omini]](era-mix·시점)·[[rf_fix_production_reproduction]].
- 회고 §4.4: C_rfeditdir·C_onetakeovercorrect·C_photozerosec·C_multicutlayers.
- 근본파일: cameraman.py(`_vlm_post_render_caption_rewrite` 3673 video-VLM·`_run_editor` 4173·`_apply_edit_plan` 4304·post-editor cap 6361·`_rf_action_grounded_captions` 2853)·producer.py(`_rf_cap_cuts` 2735·`_rf_body_budget` 2704·`_rf_structure_issues` 2711=era게이트 추가지점)·writer_realfootage.md(연출팔레트 183·동등후보 198).
