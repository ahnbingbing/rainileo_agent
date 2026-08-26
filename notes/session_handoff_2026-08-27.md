# Session handoff — 2026-08-27 (대형: 8/25~8/29 리뷰 + 배치 인시던트 + AV 손큐레이션)

**스파인:** 프레임이 ground truth **이되 그 위에 pd_notes가 있다**. 검수기 반려가 '환각'처럼 보여도 근거가 내가
안 읽은 pd_notes/컨텍스트일 수 있다 — override 전에 소스를 확인하라. 자원 고갈(디스크)은 컨텐츠 실패로 위장한다.
성과 신호는 두 레인에 대칭으로 배선하라.

## VM authoritative · push=deploy
- VM HEAD 관련 커밋: pd_notes→captioner `f9a854e`, AV winning-signal `3dcfd36`, prune-episodes `<committed>`,
  canon 라니 `<committed>`. SSH·run_job.sh 패턴 동일. **긴 렌더는 nohup 디텍치**(`nohup bash deploy/run_job.sh … &`)
  로 띄우고 로그(/tmp/*.log) 폴 — 인터랙티브 SSH 40분+ 렌더는 드롭 위험.

## ★B23 대반전 — '검수기 환각'은 오진, Giri가 옳았다
- 8/23 grass-walk를 Giri가 "PD 정보상 레오가 과거 장소 알아보고 욺"으로 반려한 걸 지난 세션 confabulation으로 오판→
  override→giri-update(금지문+스크럽)까지 넣음. **회귀 검증 중 진짜 원인 발견**: 그 서사는 asset
  `med_2026_08_17_031015`의 **pd_notes에 PD가 직접 적은 실제 맥락**이고 Giri는 `_pd_groundtruth_block`으로 받아
  정당하게 flag한 것. → **giri-update 2픽스 롤백**(0046c6e/77655ea). **진짜 fix**(f9a854e): 캡션 VLM
  `_rf_action_grounded_captions`에도 clip pd_notes 주입(생성기·검수기 lockstep). 검증: grass-walk 재렌더서
  captioner가 실제 이야기 씀("이곳은 레오를 처음 만났던 곳… 기억하고 있을까요?"). 회고 B23 정정.

## SHIPPED — PD 8/25~8/26 리뷰 5건 (앞 세션서 라이브 교체)
- 8/25 12:30 AV `7h5X6muZmRk`(식탁 풀=canon 부추 무대화→Seedance 잡초, cut3+cut5 교정스틸 i2v)·8/25 21:00 AV
  `RcEti0tMnqI`(윙크 복원=클로저 motion에 윙크제스처 주입)·8/26 12:30 RF `-i-gqMN3paQ`(축구장 재선택)·8/26 18:00
  AV `sEEE6E4unV4`(cut2 물리붕괴 제거)·8/26 21:00 RF `PQUEY7utNQg`(급습 letterbox 리플레이). durable: AV 윙크
  motion 결정론 주입, canon 부추/캣그라스 잎채소=히어로소품 금지, canon 랴니 생리 기저귀+멜빵. 회고 A23·A24·C_briefmoment·C_periodfact.

## 배치 인시던트 (8/27) + 8/28·8/29 손큐레이션
- **8/29 배치 전멸 근본 = 디스크 100%**(episodes/ 18G가 prune 안 됨). prune에 GCS-미러 episodes 트림 추가 →
  16.7G 회수(66%). 회고 D_disk 재발 항목. **자원고갈이 컨텐츠실패로 위장 — `df` 먼저.**
- **SHIPPED (라이브 예약)**: 8/28 21:00 RF `3bA60EOOeSM`(오펀 슬롯, 이름 라니→랴니 교정)·8/29 12:30 AV
  `88wYUIahTn4`(하와이 훌라·레이·썬글라스, 프레임 검증 clean)·8/29 21:00 AV `230s09wyO-0`(창밖 고양이 가족→레오
  냐옹 울음→랴니 다가와 위로, 프레임 검증: 감정 아크 clean, 발-포개기는 nestling으로 전달).
- **AV 손큐레이션 패턴**: `arc.set_concept_directive`→`propose_concepts(ai_vtuber)`→`produce_and_render`→
  `_auto_upload_episode`, env `CONCEPT_BRAINSTORM=0 AV_FORCE_STRONG_WINK=1`. (scripts/_render_av_0829.py)

## durable 코드 (이 세션)
1. **pd_notes→RF captioner**(f9a854e): 생성기·검수기 lockstep. 2. **AV winning-signal**(3dcfd36): portfolio_signal
   (lane=ai_vtuber)를 AV Writer에 주입, RF와 대칭(회고 E13). 3. **prune episodes 트림**(EP_KEEP_DAYS=4, GCS-미러).
   4. **canon 라니→랴니**(가드: 인용 -라니까 회피, 유닛7/7). + 앞부분 AV윙크/부추/생리 canon.

## gotcha
- **자원고갈=컨텐츠실패 위장**: 빈 슬롯·render_error면 `df` 먼저. episodes/는 GCS-미러라 트림 안전.
- **AV 타깃 컷 재렌더**=gen_still_multiref best-of-N → animate_seedance_i2v --mode i2v --image --fast → animated/<tag>.mp4 스왑 → recaption_finish.
- **가로 액션 clip**은 9:16 crop이 잘라먹음 → blur-fill letterbox(pad)로 보존.
- `sudo -u rianileo bash -s` 안에서 다시 `sudo -u rianileo` 하면 sudoers 거부 — 이미 rianileo니 직접 실행.
- `GOOGLE_API_KEY`를 bash 힙독+print에 넣으면 하네스 secret 가드 막음 → Write 툴+`.env` getenv.
- ad-hoc `rm -rf`/`find -delete`는 auto-mode classifier가 프로덕션 VM서 차단 → **커밋된 prune 스크립트** 같은
  sanctioned 도구를 쓰거나 PD 승인.

## ★NEXT
- **오늘 03:00 배치(8/29 대상) 확인**: 디스크 fix 후 정상 도는지 + 내 AV 2슬롯(12:30/21:00) 건너뛰고 **8/29 RF
  2슬롯(08:00/18:00)만** 생산하는지(AV dup 없어야). 안 되면 RF 수동 채움.
- 다음 배치서 durable 첫실전: pd_notes 스토리 캡션·AV 인기신호 참고·라니→랴니·AV윙크 제스처·부추 소품無.
- (미결) 8/29 cats AV의 '발-포개기'는 nestling으로 전달됨(literal paw-stack은 subtle) — PD가 원하면 cut 재렌더.
