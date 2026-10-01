# Session handoff — 2026-10-01 (RF grammar 캡션 그라운딩 근본 + 색보정 + AV Seedance 비용 + Slack 테일러링)

**스파인:** PD의 네 신호가 각각 다른 근본을 드러냈고, 관통 교훈은 **"대체 경로/기본 승격은 옛 경로의
계약을 자동 상속하지 않는다"** + **"재현 불가/저품질-영향 결함은 재시도 태우지 말고 PD에게 알려라
(기준=품질영향, 결정권=PD)"**. 알림은 PD가 보는 곳(board 채널)에 닿아야 알림이다 — write-only 플래그는
알림이 아니다.

## VM authoritative · push=deploy
- **VM HEAD `c59196f`** (전부 배포·검증). cron 유휴.
- 배포=`git push origin main`(deploy.timer 2분 폴). 운영 진실=VM DB / 라이브 YouTube(로컬 DB stale).
- ★PD 액션(선택)=`/etc/rianileo/deploy.env`에 `SLACK_WARN_CHANNEL=<id>` 지정하면 워닝이 그 채널로;
  미지정 시 **board 채널**(SLACK_BOARD_CHANNEL=C0B9NKCM679)로 감(기본, 테스트 전송 확인).

## SHIPPED (시간순 · 전부 배포)
1. **RF grammar 캡션 그라운딩 근본**(e164576): v2 `produce_grammar_episodes_shared`(9/30 RF 기본 경로)가
   캡션 그라운딩을 아예 안 했다 — subject/location 그라운딩+`_grounding_violation` 가드는 옛 rolling 경로에만
   배선, v2 승격 때 소실, story는 ungrounded base_copy 재사용 → 캡션 100% story-first(10/1 21:00 "둘이 멈췄다"
   over moving Leo / 침대→쇼파 날조 / 북엔드 불일치 / 폰트 축소). Fix=rolling 그라운딩 전량 shared에 포팅 +
   **신규 clip-level 모션 신호**(clip_motion_peak→calm/moderate/high) Writer 배선 + `edit_grammar_copy.md`
   (모션정직·one-setting·북엔드 회귀·surface 충실) + `_build_story_from_beats` body 폰트 균일.
2. **surface 충실 + velocity 면제 + co-star guard**(34ab5ab·0ead87d·cada90e): 침대 날조 백스톱; velocity(2 title-hit)는
   subject-erasure 면제; subject-erasure는 둘 다 ≥2컷 co-star일 때만(mostly-one-pet meme 오탐 방지).
3. **impact_edit 스크래치 누수 정리**(e8af5ec): `assemble()`가 tempfile 안 지워 8편 배치가 66개(~8GB) 누수→
   디스크 100%→렌더 실패(자원고갈=빈슬롯 위장). 성공경로 rmtree.
4. **색보정: 실사엔 걸지 마라 — club(velocity)만 예외**(1f30314): cinematic(teal curves+vignette)/natural(eq)이
   실사를 뭉갬(PD "다 날아가·아주 어둡거나"). render_segment grade!='club'→색 무보정(지오메트리/모션FX 유지).
5. **AV Seedance 비용 2× 근본**(b0e8d8e): per-cut `_gate_and_heal`이 `_cut_character_ok`(랴니 블레이즈)를 먼저
   검사→Seedance가 늘 과폭 렌더→거의 매 랴니 컷 실패→힐 1회 재렌더도 똑같이 실패→best-effort 유지 = 매 컷
   정확히 2× Seedance(9/26·28·30 확인). Fix=마킹/캐릭터를 **advisory**(PD veto 플래그+힐 스킵), 재렌더로
   실제 개선되는 scene/action/feeding만 힐(AV_HEAL_MARKINGS=1 구복원).
6. **Slack 테일러링**(0be90ac·c59196f): `agents/notify.py` warn()→board 채널. 마킹 best-effort 컷은
   `_run_i2v_pipeline` 말미서 에피소드당 1회 통합 워닝(전엔 write-only 플래그라 PD 도달 0). 노이즈 감량=
   launch `sp()`+selfheal `cap()`이 `[N/M]` 스텝 라인(~40/배치) Slack 억제(로그·classifier 유지,
   SLACK_VERBOSE=1 복원).

## 라이브 상태 (검증 완료)
- 9/30~10/2 grammar RF 전부 grounded + 올바른 색(story/meme true color, velocity club). 9/30+ 예약
  story/meme는 true-color 재렌더 교체 완료(3 story + 4 meme).
- 9/30 08:00 `i7PKxSlSX4s`: 랴니 없는데 제목 랴니 → 근본=ungrounded src0A 기본값+그라운더가 사람 검은팔을
  프렌치불독 오탐. 제목/설명 레오-only 교정(공개영상 비파괴)+3클립 pd_correct_asset durable 교정.

## 조회수(view) 현황 — PD "요즘 묘하게 안 나와"
- **데이터상 하락 없음**: 최근 공개편 대부분 ~1000 조회(내 재렌더 포함 bUaKmjfy2aA=1025·7DBmrrjqJF4=999·
  i7PKxSlSX4s=1024). 48h 주간평균 W36~38 ~730 안정/상승. `6wkc_hRB8uw=20`은 10/1 08:00 방금 공개라 집계 전.
- **구조 이슈(장기)**: 채널이 Shorts 피드 베이스라인 **~1000에 정체**(93 subs, 브레이크아웃 없음). Shorts는
  썸네일 편집 불가 → CTR 레버 無 → **트렌드-라이딩+훅+타임슬롯**이 실질 레버.
- **돌파 3레버 배선(main a768bfb, 전부 built-but-dormant 되살림; C_dormant)**: ①**trend_feed가 7월에 죽어 있었다**
  (crontab 미등록→trends 2~3개월 만료→시의성 AV가 일반 AV 폴백). 데일리 02:30 크론 등록+수동1회로 fresh 트렌드6개.
  ②**밴딧 미적용**→`launch_v2.day_plan`에 `stabilized("timeslot")` loop-closure(이긴 슬롯에 AV 스왑, sparse면 무동작,
  V2_BANDIT_SLOT_STEER=0). ③**RF 트렌드 미소비**→`_rf_trends_hint`로 RF도 선택적 라이딩(footage 맞을때만).
- ★교훈=성장 레버는 빌드가 아니라 가동+측정이 끝 — 크론/배선 없는 기능은 조용히 감가. impressions/CTR은 Studio서만.

## NEXT
1. 다음 AV 배치서 **BytePlus 콜 ~2×→1×** + Slack 스텝스팸 사라짐 확인(10/1은 AV 렌더 0이었음).
2. board 채널서 마킹 통합 워닝 첫 실전 확인(과다하면 임계 조정).
3. 다음 03:00 배치서 RF grammar 캡션 그라운딩+true color 첫 full 실전 스팟체크.
4. 조회수 브레이크아웃 전략(PD와 방향 논의).
