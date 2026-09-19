# Session handoff — 2026-09-20 (PD 리뷰 대형세션: 성기 AV·같은-footage 당일배치·RF 신선풀 근본 / 그리고 캡션 그라운딩 근본 → pd_notes+gpt-4o-mini 설계 확정)

**스파인:** PD가 라이브/예약 영상 2건을 지적하며 시작(①AI 영상에 랴니가 남자로=수컷 성기 ②같은 영상 3컨셉 당일 배치)했고, 파고들수록 **더 깊은 근본**들이 드러났다 — RF 빈슬롯의 진짜 원인(신선풀 압살)과 캡션이 실재를 배신하는 그라운딩 근본. 관통 교훈: **표면 증상(고갈/실내오라벨)이 근본(샘플 구조/버려진 사람-설명)을 가린다**, 그리고 **설계 방향은 human-in-the-loop이 필수**(에이전트 혼자였으면 brittle·비싼 오답을 출하했을 것 — 회고 §6 #18).

## VM authoritative · push=deploy
- **VM HEAD `a1088c2`** (55eb300 grammar-OFF 홀드 + a1088c2 RF 신선풀). deploy.timer 2분 폴 정상(세션 중 두 커밋 다 pull 확인).
- 하루 스케줄(KST): 03:00 launch_selfheal / 09:10 slot_topup / 09:40 pd_reviewer. LAUNCH_LEAD_DAYS=2.
- ★수동 렌더 gotcha: systemd-run은 **반드시 `bash -lc`**(로그인 PATH→`~/.local/bin` 최신 ffmpeg). /usr/bin ffmpeg 5.1은 `text_align`(drawtext, ffmpeg7.0+) 미지원 → burn_captions/recaption_finish 실패. env 직접쓰기(EDIT_GRAMMAR_MODE/VLM_MODEL)는 auto-mode classifier가 차단 → git-default 경로.

## SHIPPED (라이브 반영)
1. **성기 AV 교체** — 9/21 08:00 "물속 슈퍼히어로 랴니" AV cut4_peak(공중 히어로 포즈, 배가 저각도 카메라 향함)에 Seedance가 수컷 성기 환각. 근본=랴니 female/무성기 하복부 가드 부재(no-tail은 있는데 성별 가드 없음). Seedance ref 모드 그대로(ryani_solo.png) 프롬프트에 female+무성기 가드만 넣어 cut4만 재렌더 → `0LfWXK2-1Zg` 재예약, 옛 `GLYQgMc3LFc` veto. 프레임 검증(하복부 매끈). 스크립트 scripts/_fix_0921_0800_cut4_genitalia.py + _finish_0921_0800.py.
2. **같은-footage 3-grammar 당일배치 제거** — PD "같은 영상 3컨셉을 당일 배치 말라 → 3일 분산". 이미 예약된 set B(09-20)·set C(09-21) triplet 6편을 09-20/21/22로 **재예약(재렌더 0, YouTube publishAt만 이동)**: 각 날 = 서로 다른 footage 2세트. + grammar 기본 **OFF 홀드**(`55eb300`, rolling-window 나올 때까지 새 triplet 안 생김). 갭 09-20 18:00 표준RF 채움(`611K03k-R_8`). 스크립트 _reschedule_grammar_spread_0919.py. **09-21 21:00 한 슬롯만** 급성 고갈로 비어있음(정규 cron에 위임).
3. **RF 빈슬롯 반복의 진짜 근본**(`a1088c2`) — "2000개+ 있는데 왜 고갈?"의 답: 고갈도 쿨다운도 아니고 **`_diversity_sample(available_videos,100)`의 연도-균등 분산**이 신선클립을 압살(신선 사용가능 556 → sample 26). `year_col=None`(장소×활동만, 연도축 제거)로 신선-가중. archive_videos가 옛-footage 담당하므로 신선 풀은 연도평탄화 금지. 검증 sample 26→35. (회고 §4.4 C_freshpool)

## ★ 캡션 그라운딩 근본 (설계 확정, 미구현 — 다음 세션 1순위)
PD 9/18 RF story 리뷰: 둘 다 나온 나들이인데 캡션/제목이 "레오만"으로 랴니 지움 + 실외(카페 테라스)를 "집에서"로 + Giri 통과. (`m1AFJiWzGx0`, 캐스트 5클립=레오4+랴니1, outdoor/cafe.)

**근본 3겹:** ①생성기(특히 B4 grammar-copy Writer)가 서브젝트-존재/위치 미그라운딩 ②grammar 렌더 경로가 표준 RF의 per-cut VLM 그라운딩 게이트를 **우회**(우회 경로 계약 상실, D_grammarlive 패턴) ③Giri가 grammar 스킵+캡 없음.

**핵심 발견 (PD 리다이렉트로 도달):**
- **위치는 키워드 버킷으로 못 푼다** — "카페 테라스"는 실내도 실외도 아니라 최상위 VLM(opus/gpt-4.1)조차 단일 프레임서 전부 indoor로 오답. (내가 넣은 키워드 title-guard는 되돌림.)
- **함미하비의 pd_notes가 최고 그라운드 트루스** — 클립과 함께 Slack에 적는 설명이 `assets.pd_notes`에 이미 저장(slack 47%=247/525). clip5 pd_notes="카페에서 레오랑 랴니…밖에 나가서 레오는 나무도 타고"가 모든 모델이 틀린 사실(둘 다·outdoor)을 사람이 정답으로 제공. 표준 RF는 이미 씀(cameraman `_pd_notes_for`)지만 VLM 태깅·grammar 경로는 무시.
- **GPS는 대안 아님** — gps_lat/lon 컬럼 존재+home/mom 자동판정 중이나 slack이 EXIF/location atom을 벗겨 커버 5%(비디오 183/3540, 최근 15). slack_sync는 GPS 추출 자체 안 함. clip5(slack)도 GPS 없음.
- **flash가 병목** — VLM_MODEL 전부 gemini-2.5-flash. bake-off 실측: 같은 mid-프레임서 flash는 레오 놓침(ryani만)·cafe오라벨, 반면 **gpt-4o-mini·gpt-4.1-mini·claude-haiku·sonnet 전부 "둘 다"+outdoor 정확**(clip2 cafe→테라스 outdoor도 교정). gemini-2.5-pro는 thinking-mode 강제라 느림/비쌈→기각.

**★ 확정 설계 (PD 순서 명시, 전수 배치 안 함):**
1. **[먼저] 기존 slack 자산(pd_notes 보유 247개) 재태깅** — pd_notes를 authoritative로 넣어 VLM 결과(subjects/location/scene) 업그레이드. ★flash는 pd_notes 오버라이드를 무시함(로컬서 tag_assets_vlm.py에 pd_notes 주입 구현+clip5 재태깅했으나 flash가 여전히 ryani/cafe → **VM 원복, 미배포**) → 이 재태깅은 **pd_notes를 obey하는 gpt-4o-mini(또는 flash+gpt-4o-mini)**로 돌려야 실효. 대상=slack+pd_notes만. 후 asset_embed.build_index(rebuild)로 RAG 재구축.
2. **[그다음] 렌더/편집 시점 그라운딩** (에피소드가 쓰는 몇 클립만=싸고 출력직결): **pd_notes + gpt-4o-mini 다중프레임(컷 전체 span)**을 함께 돌려 피사체 유니온(둘 다)+위치 확정→자막 그라운딩. cameraman per-cut 게이트(현 flash·2프레임) 업그레이드 + grammar 경로도 이 게이트 타게(우회 제거) + Giri 서브젝트소거/위치 캡.
- **장기: Slack → Discord 이관.** description-capture(pd_notes 패턴)는 discord_sync로 이식; 마이그레이션 때 Discord의 원본/GPS 보존 여부 확인.

## ★ NEXT (다음 세션)
1. **slack 자산 pd_notes 재태깅**(gpt-4o-mini obey) → RAG rebuild. [설계 1]
2. **렌더타임 pd_notes+gpt-4o-mini 다중프레임 그라운딩 게이트** 구현. [설계 2, 출력 직결]
3. **grammar rolling-window**(한 캐스트 3variant를 D/D+1/D+2 pin 분산 → 재활성; grammar 경로는 위 게이트도 태워야). pin 메커니즘=launch.py `_pinned_episode_for`.
4. **해부 가드 durable** — character_sheets.md 랴니=female+무성기 하복부(no-tail 동급) + director/cameraman 주입 + Giri cap.
5. 09-21 21:00 빈슬롯 정규 cron 채움 확인.

## 메타 (회고 §6 #18 enrich)
이 세션의 근본들은 **에이전트 혼자였으면 못 갔다.** 위치=키워드버킷·전면 gemini-pro·전수(25,904) 재태깅·손큐레이션 같은 brittle/비싼 손쉬운 길을 연달아 제안했고, PD가 "테라스는 버킷 안 맞아"·"pd_notes가 그라운드 트루스"·"가장 싼 모델"·"렌더 시점만, 전수 아님"으로 매번 리다이렉트해 근본에 도달. **설계 결정은 자동화가 성숙해도 human-in-the-loop이 필수.**

cf. 메모리 [[grounding_pd_notes_gpt4omini]] · 회고 §4.4 C_freshpool·C_grounding · §6 #18.
