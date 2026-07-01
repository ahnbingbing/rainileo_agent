# Session handoff — 2026-07-02 (새벽)

큰 세션. AV 풀아키텍처 A/B(기각) → 콘티 에이전트 신설·배선 → 7/2 배치 2편 PD리뷰 재작업·업로드
→ BGM 다양성 + 채널 핸들 변경. durable 픽스 다수 main 반영.

## ★ NEXT (다음 세션 첫 작업)
1. **7/3 00:00 배치 스팟체크 — 콘티 배선 첫 적용 확인.** `CONTE_AGENT` 기본 ON으로 켰다
   ([[conte_cuesheet_agent]]). AV 회차가 **컷마다 프레이밍이 달라졌는지**(기존 전 컷 같은 MCU 단조
   해소) + **BGM이 다양해졌는지** 눈으로 확인. best-effort라 배치를 깨진 않지만, 콘티가 실제로
   Director 산출을 끌어올리는지가 관건. 별로면 `CONTE_AGENT=0` kill switch.
2. **이미 예약된 7/2 회차의 범퍼 핸들.** 핸들을 `@ryani_n_leo`로 바꿨지만(범퍼 재빌드) **이미
   어셈블된 회차는 옛 핸들이 베이크**돼 있다(오늘 업로드한 café v3/AV21 v2 포함 — 이건 새 범퍼로
   재어셈블됨, 하지만 그 외 예약분은 옛 것). 원하면 예약 회차 일괄 재어셈블+reupload.

## 오늘 라이브 반영된 회차 (7/2 배치)
- **café RF18 v3** `QP_1FOwjF_g` (7/2 09:00) — 도둑동생 편. 엔딩 "카페 queen 랴니"(도난 지움)→
  **완전범죄 아크**(테이블 간식→랴니 "기다려" 대기→착한 한입→레오 마구먹방→랴니 멀뚱→레오 시치미).
  레오 POV 대사는 "랴니엄마" 호칭. recaption_finish로 강제(RF는 캡션 재도출됨).
- **AV21 꽃-꼬리 v2** `rGZf-Oj-U9s` (7/2 12:00) — PD 아이디어. 랴니 놀자 호들갑→레오 발라당→
  **랴니 뒤돌아 꽃-꼬리 씰룩**(무꼬리+꼬리자리 데이지)→레오 시선강탈→윙크. 마지막캡션 "오늘도 햅삐♥".
  ★썸네일 적나라(뒷태 엉덩이)→cut1 플레이바우 정면으로 교체+set_thumbnail 성공.

## SHIPPED (durable, main 커밋)
1. **콘티(큐시트) 에이전트 신설·배선** [[conte_cuesheet_agent]] — `agents/prompts/conte_cuesheet.md`
   (편집자 관점 샷 설계 전용) + `writer_director.run_conte`(AV전용, Writer→콘티→Director). 콘티가
   컷별 `cuesheet`(shot_size/angle/camera/blocking/start→end/depth)+coverage 설계→Director가 실현.
   `director_shots.md`의 shot_size 고정룰=체인전용으로 스코프축소, 몽타주 varied 필수로 supersede.
   3사 배심 섀도우서 현행 Director 만장일치로 이김. `CONTE_AGENT` 기본 ON, best-effort.
2. **AV 첫+끝 프레임 interp 아키텍처 = 기각** [[av_firstlast_interp_rejected]] — 지어서 A/B 렌더했으나
   합성스틸 의존이라 2D/원근깨짐(AV_PRECISE_STILL과 같은 문제). 3사배심도 실사감 A 만장일치. 코드 미커밋
   폐기. 설계+판정 `notes/av_firstlast_frame_architecture_design.md`. 다른 배경-베이크 수단 없인 재시도 금지.
3. **6/30 배치 리뷰 픽스** [[batch_0630_review_fixes]] — canon Ryani 코 주름 3곳 + **무꼬리 전-포즈 강화**
   (플레이바우/뒷태서 꼬리 헛것 금지, 기쁨=엉덩이 위글). realfootage_concept **간식도둑+시치미 패턴**
   (착한 피해자 절제 vs 범인 탐욕=부조리 웃음). reviewer **"사건 무마/전제 부정" Giri캡 ≤6**.
4. **caption POV 호칭 규칙** — 캐릭터 POV 대사는 화자 자신 호칭(레오→랴니="랴니엄마", 내레이터=3인칭).
5. **BGM 다양성** [[bgm_expanded_map]] — `_pick_bgm_track`: 얇은 mood(1-2곡)면 전체 89곡 풀로 확장 +
   최근 16곡 회피(bgm_by_video.json). 검증 9/10 유니크. re-render 안정성 유지.
6. **채널 핸들 `@ryani_n_loe`→`@ryani_n_leo`** — build_bumpers 기본값+범퍼 재빌드+CLAUDE.md/packaging.md.
7. **썸네일 적나라 배제** — pick_thumbnail VLM 판정에 "엉덩이/생식기 정면 클로즈업 배제, 얼굴 앞/옆" 규칙.

## 상시 주의
- 맥 네트워크 불안정=렌더 재시도 잦음(컷당 수 분). GCP 이관(별도 세션 LOCKED)이 근본해결.
- 일회성 `scripts/_*.py` 렌더 스크립트는 main 커밋 제외(노이즈). 범퍼 mp4는 gitignore(로컬 에셋) —
  deploy 시 build_bumpers 재빌드 필요(기본값은 커밋됨).
- 커밋은 PD 요청 시에만. RF 캡션은 render_card가 재도출하니 특정 캡션 강제는 recaption_finish.
