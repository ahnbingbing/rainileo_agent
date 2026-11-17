# Session handoff — 2026-11-17 · 편집 천장(캡션 위트·split) + RF 품질바닥 근본수정

**한 줄:** "11/17 21:00 하나 만들어"로 시작 → RF 반복/실패의 **근본 여러 겹을 코드로 수정**하고,
PD의 "편집 더 재밌게" 방향(split-screen·캡션 위트 3보이스)을 **배선+검증**했다. 12커밋 전부 VM 라이브
(HEAD `c1c10ea`). 남은 건 **속도 레버**(아래 ★) — 다음 배치 돌려보고 느리면 착수.

---

## ✅ 오늘 배포 (가역 env 전부 명시, 전부 PD 2026-11-17)

**품질 바닥 올리기 (실패/반복 근본)**
- `46a8328` **HLG/SDR 색 VUI robust** — 전부-SDR 에피만 trc/primaries=unknown으로 나와 일부 플레이어서
  허옇게 뜨던 근본. assemble에서 모든 입력에 `setparams` bt709 프레임스탬프. (※아래 §Colour)
- `46a8328` **인트로 범퍼 제거 = outro_only 기본규칙** — `_bumper_arm` 기본 outro_only(A/B 종결).
  잊힌 enforcer 2곳(caption_salvage·Giri 프레임샘플 intro오프셋)도 정렬. `BUMPER_MODE={intro_outro,ab}` 복원.
- `63e5991` **junk-gate** — RF 재시도의 두 ship 경로(소진·타임아웃)가 Giri-실패본(3/10·4/10)을 그냥
  예약하던 'junk금지' 위반. `RF_BESTEFFORT_FLOOR=7` 미만이면 슬롯 비움. AV는 2026-06-10부터 이미 통과분만.
- `63e5991` **마일스톤 era-창 ±7→±1** (`MILESTONE_POOL_DAYS`) — ±7일이 입양일 ±2주를 옛footage로 오염 → 0컷.
- `8127c6b` **구별-테마 쿨다운** — 수영 같은 '구별 소재'를 코퍼스 토큰 DF로 판정(산책/낮잠 staple=면제),
  14일내 공개편과 겹치면 soft-fail. 7월 "footage만 신선하면 OK"와 화해. `THEME_COOLDOWN`/`_DAYS`/`_DISTINCTIVE_MAX`.
- `c404fde` **vis_phash 백필+배선** — 시각-유사 쿨다운이 vis_phash 0% 커버리지로 **장님**이던 것(유사 산책컷 통과
  근본). 프리웜에 recent-first 백필 배선(`LAUNCH_PREBATCH_VPHASH`). 1회 백필로 최근 0%→91%.
- `ea53456` ★**유령자산 필터 (gutted-render 근본)** — DB엔 레코드 있는데 파일이 local도 GCS도 없는 자산 ~3%가
  선택풀에 남아, Writer가 그걸 고르면 render가 "missing source"로 드롭→gutted→슬롯실패→재롤 thrash. 세션 내내
  그루밍·마일스톤·split데모를 막던 진짜 근본. `_gather_context` 반환전 네 풀을 **가져올수있는것만**(local 즉시+
  GCS `exists` HEAD) 필터. 검증 미가용 3→0. `RF_AVAIL_FILTER=0` 복원. (깊은축=Mac→GCS 동기화갭은 별도 인프라 follow-up.)

**편집 천장 올리기 (PD "더 재밌게")**
- `d5fcc95` **split-screen 프로덕션 배선** — per-cut 효과(켄번스/배속/프리즈)는 됐지만 split 0/98. 근본=프로덕션
  RF 스키마(realfootage_concept.md)에 split_*가 아예 없었음(하류 cameraman materialize+_prerender_split_cuts는 11/13
  재빌드로 멀쩡). 스키마+능동지시 추가 + `_apply_rf_split_compose`(페어 이중사용 흡수·무효 다운그레이드). `RF_SPLIT_COMPOSE=0`.
- `265c678` **split 모션 5변주** (PD "상하만 안됨") — opposing/opposing_rev/same_left/converge/diagonal, 클립경로
  해시 결정론. top/bottom settle·블러 no-black·양쪽 라이브 유지. `RF_SPLIT_MOTION_VARY=0`.
- `85ceedf` **split 능동화** (C_rfeditdir 허용≠행동 재발) — 스키마만으론 Writer가 split 0/3(대비컨셉도). 프롬프트
  "대비beat=split 기본"(번갈아자르기 금지) + structure-gate nudge(두펫+대비+split없음→재제안 유도, `RF_SPLIT_NUDGE`).
  재스모크 2/4 대비컨셉이 split 생성·단일주체 2개는 안함(정확).
- `c1c10ea` **캡션 위트 3보이스 로테이션** — ★PD 핵심교정="캡션 재밌게=글씨스타일 아니라 **내용(글)**을 재밌게,
  글씨는 손글씨로 잘보이면 됨. 세 톤이 돌아가면서". realfootage_concept.md에 3보이스 정의(따뜻한공감관찰/1인칭시트콤/
  밈드립)+예시, `_rf_caption_voice(target)`가 날짜로 로테이션→"이번 회차 보이스=X" system 주입. 설명조("…삼매경") 탈피
  강제(도사체·overshoot 금지선 유지). `RF_CAPTION_VOICE`로 한 보이스 핀(테스트).

**회고:** `bda7f66`·`4b7fb53`·`34baee9` — C_junkgate·C_sdrvui·D_bumperab·D_themecooldown·**C_poolavail** 기록.

**실제 예약물(오늘 중):** 11/17 21:00 `NLe9_a0N3TM`(아기레오 그루밍, 아웃트로-only, HLG정상) · 11/18 18:00
제목 API수정(보라우비 랴니+곰인형 레오, 재렌더 불요).

---

## ★ 다음 세션 TODO — 속도 레버 (PD: "오늘 돌려보고 안되면 이거 해라")

PD 관찰: RF는 에피소드당 느리다 — 깨끗하면 **컨셉~2min(Opus 3-pass) + 렌더~8min + Giri ≈ 10~15min**,
thrash하면(Giri거절·구조게이트·재제안 반복) 30~60min. 00:00 cron이라 비대면이지만 thrash가 낭비.
**먼저 실제 에피 1건을 end-to-end 계측**([[rf_render_stage_profiling]] 방식, STAGE_TIMING=1) 후 큰 레버부터:

1. **재제안 thrash 줄이기 (가장 큰 레버)** — 한 슬롯이 구조게이트+split-nudge+Giri재시도+에디터루프를 겹쳐
   10+회 재제안하면 wall-clock 폭증. 레버: `RF_STRUCTURE_MAX_TRIES`·`EDITOR_MAX_LOOPS`·Giri 재시도 횟수/조건,
   그리고 **왜 Giri가 반복 거절하는지**(오늘 split데모가 3/10 반복) 근본 — 품질 vs 속도 트레이드오프라 측정 먼저.
   오늘 배포한 유령필터·junk-gate가 gutted-render 재롤은 이미 줄임(그 부분은 호전됐을 것).
2. **컨셉 생성 ~2min** — `_propose_realfootage_singlepass`가 Opus로 클립 100+개 읽고 전체 컨셉+캡션 작성(3-pass).
   레버: 서핑 클립수 축소(RF_PHOTO_POOL_MAX 등)·프롬프트 캐싱 재사용(D_rfcache 참고)·일부 경량모델(품질 Writer라 조심).
3. **렌더 내 VLM 그라운딩 직렬구간 병렬화** — rf_render_stage_profiling에서 VLM 그라운딩이 render의 ~43%였음(trim-proxy
   후). gate/action/tail-trim/grounded/face 직렬 → 병렬화 여지.

---

## 지금 확인할 것 (다음 00:00 배치 스팟체크)
- **캡션**: 보이스별로 위트있게 나오나(설명조 탈피)? 톤 과함/부족? → `RF_CAPTION_VOICE` 핀으로 조율.
- **split**: 대비 훅 회차에서 split 컷이 실제로 뜨나? 모션 변주 도나?
- **gutted/0컷 실패 사라졌나** (유령필터 효과) · junk-gate가 저품질 슬롯 비우나.
- 렌더 wall-clock — thrash 나면 위 속도레버 1번부터.

## 미해결/follow-up
- Mac→GCS 동기화갭(유령자산 원천) — 일부 slack/icloud 자산 미러 안 됨(인프라).
- 편집 천장 나머지: ②재미있는 background/캡션바(보류) ③fal.ai v2v 오버레이(유료·납작금지·비용 사인오프 후 파일럿)
  ④RF+AV 하이브리드+재미BGM(MV식, AV는 양념컷만 — 단독 저조회 주의).
- split 데모 풀렌더로 "Giri통과한 split 완성본" 못 봄(유령필터 전 thrash). 유령필터 후 재시도 가능.

cf 메모리: [[rf_editing_direction_memories]]·[[deliver_visuals_via_open]](PD는 SendUserFile 못 봄, open+Desktop)·
retrospective C_poolavail·C_junkgate·D_themecooldown·D_bumperab·C_sdrvui.
