# Session handoff — 2026-07-08 (board-bot 대수술 + rerender 근본수정 A→B→C→D + 07-06~08 배치 손수정)

초장기 세션. 시작은 "board 봇이 PD 리뷰를 '좁혀서 다시 물어봐'로 떠넘긴다"는 진단이었고, 끝은
조선시대 부침개 AV까지. 스파인: **봇이 PD 리뷰를 제대로 처리 못 하고 → 그 아래 rerender가 원본을
안 지키고 새로 뽑는 결함까지 드러나 → 파이프라인+슬랙봇을 근본 수술하고, 그 사이 3일치 배치(07-06/07/08)를
손으로 정확히 다시 만들었다.** 모든 durable 변경은 커밋+배포됨(push=deploy, VM이 2분 폴로 pull).

## VM이 authoritative. push=deploy.
`git push origin main` → deploy 타이머가 pull→smoke→봇 재기동. 검증은 **VM HEAD**로(push 성공 ≠ 배포).
VM 렌더 실행에 필수: `PATH=/home/rianileo/.local/bin`(ffmpeg N-125444, drawtext `text_align` 지원 —
`/usr/bin/ffmpeg` 5.1.9엔 없어 캡션 번인 전멸) + `source /etc/rianileo/env`(API 키). `render_card`는
카드 `state='approved'` 요구. 렌더 부하로 SSH가 255로 죽으면 `gcloud compute instances reset`이 유일한 kill.

## 1. Durable ships (전부 배포)

### 봇(slack/board_agent.py) — PD 리뷰를 실제로 처리
- **멀티건 리뷰 디컴포지션 + 막다른 폴백 제거**(43376ec): `_AGENT_MAX_STEPS` 5→10, 마지막 스텝 final
  강제, "좁혀서 다시 물어봐" punt 대신 board_escalations 인계.
- **즉시 ack + 마일스톤 스트리밍**(ab318ff/b71ca6a/4171218): 수신 즉시 "👀 받았어요", 렌더 진행을
  read/action 툴별로 흘림. **roadmap B**: 렌더 마일스톤을 workroom이 아니라 **PD board 스레드로**
  (`agents/board_progress.py`, `_act_rerender`가 `BOARD_PROGRESS_*` env 전달).
- **슬롯 지도 자기해결**(ed48d5b): `youtube_schedule`이 그날 4슬롯 라벨(비공개/빈 슬롯 포함)을 붙여서
  봇이 라벨을 스스로 잡음(되묻기 금지).
- **direction 실은 rerender**(16d5c92): PD 방향을 `PD_RERENDER_DIRECTIVE` env로 실어 재렌더가 반영.
  날짜 가드는 미래 배치 예약(set_concept) 전용 — 이미 만들어진 슬롯 재렌더는 board 지시가 최우선.
- **레인별 비용**(8523af3): RF는 ~$50 아님 거의 무료.
- **봇 결함 4종**(dffe4fc): ①개수 준수(하나면 하나만, ≥2 금지) ②이미 준 정보 되묻기 금지 ③실제 stop
  (`stop_renders` 툴이 렌더 프로세스 pkill — "멈췄어요" 거짓말 종식) ④슬롯 배정과 다른 레인은 escalate로
  CLI 인계(엉뚱한 레인 blind 재렌더 금지).

### rerender 근본수정 (roadmap A, b904611)
- **원인**: 봇 rerender가 **모든 리뷰를 통째 재생성**으로 처리 → PD "캡션 고쳐"(영상 멀쩡)를 컨셉부터
  다시 뽑아 **다른 클립으로 스왑**(2017 풀숲→2024), 그 재선택이 **그날 공개된 영상 클립을 재사용**.
- **A1/A2 mode=caption|rebuild**: caption은 `scripts/recaption_slot.py`가 **원본 슬롯 클립을 핀**
  (`render_card use_brain=False` on its own concept) + 캡션만 재그라운딩(Layer2). rebuild만 재선택.
  `_rf_action_grounded_captions`가 `PD_RERENDER_DIRECTIVE`를 읽어 방향 반영. **RF1230·18:00 낮잠에서
  실전 검증됨(원본 보존+바닥 정정 성공).**
- **A3 클립 cooldown 뿌리이전**: `launch_pipeline`이 최근 7일 공개분 클립을 `batch_used_assets`에 시드
  (단일-슬롯 재렌더의 빈-세트 구멍 해결, 모든 렌더 공통).

### Giri 게이트 (roadmap C + gates ①②)
- **C. 클립 재사용 결정론 게이트**(23ffb07): 이 편 클립이 최근 7일 공개분과 겹치면 점수 ≤5·수정 필요
  (회귀: known-bad 9→5, known-good 9 유지).
- **① 캡션 안 바뀜 / ② 표면·장소 그라운딩**(a6fc4b9): RF 캡션이 프레임 대부분 그대로면 "하나가 너무
  오래" cap ≤6; 캡션이 침대라는데 프레임은 바닥이면 CHECK0 거짓 cap ≤5. **프레임 판정이라 배치
  false-fail 위험 없음**(개념-데이터 결정론 게이트가 아님).
- **③ BGM**: 피커가 이미 `data/bgm_claimed.json`으로 claimed 트랙·라벨 제외. 첫 claim은 Content-ID
  특성상 사후 swap_bgm.

## 2. 손으로 다시 만든 배치
- **07-06**: PD 리뷰 4슬롯 재정비(이전 세션 이어받아 확정).
- **07-07**: 봇 rerender가 잘못 만든 것 CLI로 전량 재제작 — RF1230(원본 풀숲+동작4비트), RF2100(비오는날
  우비 산책 2016), AV1800(무지개 우산댄스 오버더레인보우, PD 컨셉), 12:30 AV(에어컨 윙크).
- **07-08**: 봇이 헤집어놓음(잘못 rerender 2개, 08:00·12:30 un-list) → reset+복구. 최종 4/4:
  08:00 `1aeZKyoX3RE`(카페)·**12:30 `-iXS4IROmNw`(🥞조선시대 부침개 대첩, 한옥·장마)**·18:00
  `Da0gpO15kwk`(바닥낮잠, recaption)·21:00 `SBq7MpAjvZE`(레오 나른한 오후, grandmompapa).

## 3. 관통 교훈
- **봇 자율 재렌더가 사람이 손으로 지키던 "리뷰한 영상 보존" 불변식을 안 지켜 같은 실패를 자동
  재생산했다** — 자동화는 사람의 암묵 불변식을 코드에 명시해야 안전. (회고 D21.)
- **캡션은 넘겨받은 클립을 성실히 분석한다 — "다른 영상"은 선택 단계에서 바뀐 것.** 캡션 스테이지를
  탓하지 말고 상류 클립 선택을 봐라.
- **reupload_episode는 옛 카드 제목을 끌어온다** → 컨셉 바꾼 슬롯은 제목/설명을 새로 써야(RF2100·AV·
  부침개 다 수동 교정). 봇의 launch_pipeline 업로드는 새 컨셉 제목이라 무관.
- **배치 false-fail 위험**: 03:00 배치 직전에 개념-데이터 결정론 게이트를 미검증 배포하면 전 슬롯을
  비울 수 있다 → 게이트는 회귀검증 후, 급하면 프레임-판정 캡(LLM)이 안전.

## ★ NEXT
1. **07-08 배치 스팟체크**(아침): 4슬롯 특히 12:30 부침개·18:00 바닥낮잠 캡션 확인. `/veto` any off.
2. **task #7 — 게이트 심화(결정론화)**: ①캡션 밀도(최종 캡션 window ≥Ns 1개면 fail — 데이터소스=
   burn 캡션 매니페스트 확인 필수) ②표면 VLM 검증(grounding gate 확장) ③BGM 안전 화이트리스트.
   반드시 03:00 배치 실데이터로 회귀검증 후 배포.
3. **오늘 밤 03:00 cron**이 07-09를 A3(클립 cooldown)+C(재사용 게이트)+①② 캡으로 **처음 프로덕션**
   생성 — 클립 재사용/캡션 안 바뀜/표면 오인이 실제로 안 나오는지 확인.
4. board 봇 `stop_renders`·레인-CLI-인계 실전 확인(다음 PD 멀티건 요청 때).
