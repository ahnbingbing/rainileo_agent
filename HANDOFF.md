# HANDOFF — 품질 + 인프라 작업 (2026-06-07 갱신)

> 새 shell은 CLAUDE.md 다음으로 이걸 읽어라. 브랜치: **approach-d-grounded-singlepass** (미머지).

## ⛔ 출력 규칙
도구 호출 코드를 답변 텍스트에 쓰지 마라. 매 턴 도구가 실제 실행됐는지(결과 반환) 확인. 말로만 "실행합니다" 금지 — 이게 과거 3일 날린 원흉.

## 운영 메모
- **producer를 동시에 2개 돌리지 마라** (DB/파일 충돌). 로컬 E2E와 Slack `/test`도 동시 금지.
- Slack 리스너는 launchd `com.rianileo.slack`로 상시 실행. 코드 바꾸면 `launchctl kickstart -k gui/$(id -u)/com.rianileo.slack`로 재시작해야 반영 (단 프롬프트 .md는 런타임 read라 재시작 불필요).
- E2E 테스트는 **실모델**로: `OPENAI_FALLBACK_MODEL` 미설정(기본 gpt-5). gpt-5-mini로 돌리면 스토리 품질 급락 — 과거 실수.
- 테스트 시 기리 retry 상한 낮추려면 `RF_GIRI_MAX_ATTEMPTS=3` (프로덕션 기본 100).

## 두 레인
- **real_footage**: 단일-패스(`realfootage_concept.md`) → `_render_realfootage_direct` → `_render_realfootage_with_retry`(기리 통과까지). 실제 클립 기반.
- **ai_vtuber**: Writer/Director(`writer_director.py`, `writer_story.md`/`director_shots.md`) → `render_with_retry`(기리). GPT 캐릭터 regen → Seedance. 원테이크+윙크 엔딩.

## 2026-06-06~07 완료 (활성)
### real_footage 품질
- 단일-패스 캡션 보존(VLM 재작성 SKIP), KO/EN 자막 분리 계층
- **크롭(인간 얼굴 절대 금지)**: `_vlm_pet_crop_filter` — 펫 bbox를 다 담는 최소 9:16 창을 만들고 사람 반대쪽으로 밀어 얼굴 제외. **회전 메타데이터 버그 수정**(추출 프레임 치수 사용). 펫 안 잘리고 9:16 꽉참.
- **zoom-freeze 제거**: ken_burns/zoom/pan은 zoompan d=dur*30이라 영상을 첫 프레임에 freeze → "정지 화면 줌인". real_footage 영상 컷에서 zoom류 효과 전부 drop(static), 실제 영상 모션 재생.
- 마지막 컷 여운: freeze 대신 원본 충분하면 실제 영상 재생
- photo_i2v: 캐릭터 모션 보장(모션 측정→부족시 재생성, standard 모델) + **ai_vtuber 랴니/레오 캐논 주입**(`_append_character_canon`)으로 캐릭터 드리프트 방지. 사진=보조재(중간 0~2개, finale는 영상)
- prop 정확성("녹색 채소 담긴 그릇"→"초록 그릇" 오표기 금지), 단일장소/저모션클립 회피/가짜인과 금지, 톤 3레인, 자막 촘촘/연속
- 기리 검수 연결(우회 해소) + 통과까지 retry, 슬랙 리포트 파리티(format_slack_report)
- VLM 태그 수정(추측금지/포즈/배경사람) + 불확실→PD 문의 큐(`pd_correct_asset --list-uncertain`)

### ai_vtuber 품질
- 윙크 엔딩: 5→7초, 직전 장면 연결 비트+윙크 holding(뜬금없음 해소), **wink_subject를 Writer LLM이 스토리 기반 결정**(약올린 주동자가 윙크; `_pick_wink_subject` 패턴은 fallback)
- 캡션 변별: 원테이크 동작중계 금지(setup→반응→펀치라인, 랴니 반응 활용)

### 🔴 자막-영상 불일치/누락 (심각, 양 레인 공통) — 2026-06-07 수정
- **자막 누락(공백)**: 자막 연속성 강제를 **av에도 적용**(첫 0.1s부터 컷 전체 빈틈없이, 윙크 컷만 예외). av 원테이크가 첫 ~2초 공백이던 문제.
- **재교정 안 됨("VLM 캡션 0컷 재작성")**: 캡션 에이전트 컷 tag가 captions.json 키와 안 맞아 0개 반영 → **index fallback 매칭** 추가.
- ※ 미검증: av E2E 재실행으로 확인 필요(작성 시점 진행 중).

### 인프라
- **중간영상 자동정리**: 렌더 성공 시 `_prune_tmp_workdirs`(최근 `CAMERAMAN_TMP_KEEP`=6 유지). 기존 16GB/335개 → 8개로 일회성 정리함.

## 업로드 이후로 게이팅 (지금 비활성, PD 지시)
- **아크 시스템** `agents/arc.py`: av+rf 통합 대장 + 롤링 ~1개월 시즌 플랜(계절·공휴일·트렌드·월1회 재소개·ai_vtuber 판타지/배경전환) + LLM 쇼러너 디렉티브 + 기록. **`ARC_ENABLED` 기본 off** (함수 no-op). 업로드 파이프라인+피드백 생기면 켤 것.
- **클립 4편 쿨다운**: `cards.uploaded=1` 영상만 카운트. 업로드 전이라 현재 0개(비활성). `_recently_used_rf_assets`. `cards.uploaded` 컬럼 추가됨.

## 다음 후보
- av 자막 수정 검증(E2E) → 기리 통과 확인
- (로드맵) **YouTube 업로드 파이프라인** — 이게 생겨야 아크/쿨다운 활성화 + 업로드 피드백 기반 콘텐츠 선택
- data/output/episodes(5GB)·animated_captioned 정리 정책
- av "캡션 미스매치(내용 vs 렌더)" 더 깊은 재교정 품질

## 핵심 파일
- `agents/producer.py` — propose/render, 쿨다운, 아크 주입, 기리 검수/retry
- `agents/cameraman.py` — run_real_footage_pipeline, 크롭(`_vlm_pet_crop_filter`/`_build_crop_filter`), 캡션 연속성+ko/en분리(generate_manifests), photo_i2v(`_prerender_photo_i2v_cuts`+캐논), zoom-drop, `_vlm_post_render_caption_rewrite`(index fallback), `_prune_tmp_workdirs`, `_append_character_canon`
- `agents/writer_director.py` — `_build_wink_cut`, `_pick_wink_subject`
- `agents/arc.py` — 아크(게이팅 off)
- `agents/reviewer.py` — 기리(자막공백/킥/얼굴/캐릭터 룰)
- 프롬프트: `realfootage_concept.md`, `writer_story.md`, `caption_agent.md`, `tag_assets_vlm.py`
- `agents/tools/pd_correct_asset.py` — pd_notes + `--list-uncertain`

## 테스트
```
.venv/bin/python -m agents.producer --date 2026-05-22 --style real_footage --no-slack
.venv/bin/python -m agents.producer --date 2026-05-22 --style ai_vtuber --no-slack
RF_GIRI_MAX_ATTEMPTS=3 ...   # 검증용 retry 상한
```
(05-22 = 자산 풍부, v2 재VLM 완료)
