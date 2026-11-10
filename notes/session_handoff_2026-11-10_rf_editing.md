# Session handoff — 2026-11-10 (RF 편집 방향성 전면 개선: 추억-믹스)

**스파인:** PD "왜 이렇게 편집이 안될까? 사진과 동영상을 적정히 섞으면서 하잖아." → 일반 RF 쇼츠를
Apple 사진 '추억/Memories' 편집 그래머(사진+영상 믹스·켄번스·크로스디졸브)로 올리도록 **기존
Writer/Director 파이프라인의 편집 *방향*만** 바꿈(별도 빌더 금지 — PD "나는 편집 방향성만 다르게 하길
원한거야"). 핵심 교훈 체인: 능동믹스 배선 → RAG 사진선택 → 컷수 스토리기반(원테이크 과잉교정 제거)
→ **결정적 버그 발견: 재료검증이 사진을 0.04초로 세어 추억-믹스를 숨어서 죽이고 churn을 일으킴.**

## ✅ 배포분 (전부 킬스위치 가역, main 배포됨)
| 커밋 | 내용 | 킬스위치 |
|---|---|---|
| 261800a | Writer 능동 사진믹스 directive (themed/montage엔 사진비트 1~3개 능동으로 엮음) | FORCE_EDITING_CONCEPT |
| 9b57c90 | RF assemble 크로스디졸브 전체전환 (AV경로만 있던걸 RF에) | RF_CHAIN_TRANSITION=hardcut, RF_XFADE_DUR |
| b8f4237 | RF 사진선택에 임베딩 RAG(asset_embed.find_similar) — 그날 arc_directive로 장소/시간/주제 매칭 사진을 풀 앞쪽에 머지 | RF_PHOTO_RAG=0, RF_PHOTO_RAG_K |
| 55804ac | 컷 수 = 스토리가 정한다(1~N). writer_realfootage "원테이크 blanket 기본값" 제거, long_clip_candidates 명령→정보 | — |
| **a7d3dbf** | **★재료검증(_rf_footage_gate)이 사진 켄번스 비트에 요청 초 그대로 인정(kind 읽어). 영상만 실제길이 cap** | RF_FOOTAGE_GATE=0 |
| 8cd561a·외 | 회고 C_rfeditdir·C_onetakeovercorrect·C_photozerosec | — |
+ 이전 세션 것: RF_PHOTO_FLASH=0(사진=풀길이 켄번스 줌교대)·RF_ACTION_TRIM(액션겨냥 트림)·466a410(Giri 정지덤프만 감점).

## ★핵심 교훈 3개 (회고 §4.4 기록)
1. **C_rfeditdir — 허용≠행동.** 사진금지 supersede만으론 Writer가 영상-only 디폴트로 돌아감. "적극 엮어라" 능동 directive 필요(photo_cuts 0→1↑). 다른 편집 *느낌*은 기존 콘텐츠 파이프라인의 편집 *방향*만 바꿔라(별도 빌더=그라운딩/캐릭터/캡션 계약 상실→"쪼르르 달려감" 날조).
2. **C_onetakeovercorrect — 과잉교정이 기존 좁은 가드와 겹치면 순손해.** RF 원테이크 수렴 근본=2026-06-11에 "한 클립 6-9s 쪼개 가짜몽타주" 버그 막으려다 "12s+ 있으면 무조건 원테이크 기본값" 박음. 그 버그 막는 진짜가드 이미 둘 존재(_rf_structure_issues 비인접재사용+_collapse_rf_same_clip_segments)→blanket 기본값은 중복이자 유해(정상 멀티컷=추억믹스까지 죽임). 유일 가드="한 클립 가짜 쪼개기 금지"만 남김.
3. **C_photozerosec — 정지자산 "실현 길이"=파일길이 아닌 디스플레이 시간.** 재료검증이 각 컷을 ffprobe길이로 cap하는데 JPEG은 ffprobe가 0.04s(단일프레임)→사진 켄번스비트 0초계산→추억믹스 13s floor 못넘어 리젝→churn. **컨셉 통계(컷수 3/6/4) 통과여도 소비처 게이트서 막힘 — "끝까지 돌려 결과를 보라"(PD)가 이 버그를 드러냄.** ★내가 한 번의 코드독해로 "사진 안죽임"이라 단언한 건 틀렸다(실측이 진실).

## 검증된 것
- RAG 검색 품질: "소파 낮잠"→0.922 실제 소파낮잠사진, "눈 산책"→2017 눈산책. find_similar는 완벽.
- Writer 컷수 라이브(강제없이 3회): themed_compilation 3/6/6컷(원테이크 아님), 11-09은 사진1장 엮음.
- 재료검증 수정 유닛테스트: 사진3+영상2 믹스 10.1s(리젝)→**22.0s(통과)**. 사진이 요청초 그대로 인정됨.
- 사진 총량: **22,454장**(70은 에피당 프롬프트 다양성 샘플 상한, _diversity_sample). icloud 파일도 GCS 실존(on-demand fetch 20/20·70/70 검증). 동기화 잡 불필요.

## ⏳ 미완 (네트워크 끊김 시점)
- **전체 파이프라인 end-to-end 최종 검증 진행중**: `launch_selfheal --date 2026-11-16 --slot 18:00 --lane real_footage --no-upload`(VM 로그 `/tmp/e2e_c.log`, 폴링 백그라운드 태스크 bxnk8ympb). **목표=사진수정 후 churn 사라졌나 + 사진+영상 추억-믹스 mp4 룩(켄번스+크로스디졸브) 눈으로 확인.** 결과 미확인. 재개 시: `/tmp/e2e_c.log` 끝 확인(재료검증 재작성 횟수↓ 기대)·새 `data/output/episodes/episode_rf_*.mp4`(16:45 이후) 프레임그리드로 룩 검증.
  - ⚠️ 직전 런(수정 전)은 11-16에서 20분간 5번 재작성 churn(재료검증3+구조검증1, 최근공개본 17개 하드제외로 얇은풀). 수정 후엔 사진-믹스가 floor 통과해 churn 없어야 정상.
- **IAP 터널 매우 flaky**(255 반복): ssh 자꾸 끊김. 재런 시작은 `nohup ... & disown`로.

## 다음 할 일
1. 11-16 e2e 결과 프레임검증(추억-믹스 룩 확인) → OK면 이 전체가 프로덕션 기본 적용됨(배포 완료, 다음 00:00 배치부터 실전).
2. 다음 00:00 자동생산 RF 스팟체크: 사진-믹스 실제로 나오나·churn 없나·캡션 그라운딩.
3. 판단 대기: 사진 비중 더 밀지(현재 에피당 1장 액센트=PD "내용은 영상이 진다" 교정 따른 것), 트림 겨냥(_action_trim_start)이 실제 액션 잡나 별도 확인.
4. **별개(이전 핸드오프)**: 감꼭지 MV 멸종위기사랑 디스코 컨셉 이미지 8컷(data/tmp/mv/gamkkokji_concept/) PD 아침 컨펌 대기 — 이번 세션 안 건드림.

## 참조
- 메모리: [[rf_editing_direction_memories]](전체 배선+3버그), [[rf_fix_production_reproduction]](RF 정식 재생산법), [[title_hook_coherence_generator]].
- 회고 §4.4: C_rfeditdir·C_onetakeovercorrect·C_photozerosec.
- 근본파일: writer_realfootage.md(컷수=스토리 54-92행)·producer.py(_rf_concept_photos 3315머지·_rf_footage_gate 3776사진fix)·cameraman.py(_prerender_photo_kenburns_cuts·RF assemble 크로스디졸브).
