# Session handoff — 2026-08-31 (9/1 리뷰 2 근본 + OAuth 토큰 분리 대기)

**스파인:** 결정론 교정기가 **데이터(촬영일)를 안 보면 문자열을 추측할 뿐**이다 — ground truth를
먹여 계산하게 하라. 그리고 **dedup by content는 form(형태)의 반복을 못 본다** — PD가 "왜 자꾸 X를
하냐"면 유사도 튜닝이 아니라 premise 자체를 막는 게이트가 답. 프레임이 ground truth이되 pd_notes가
그 위에 있다(8/27 스파인 유지).

## VM authoritative · push=deploy
- **VM HEAD 5a20184** (durable 2건 배포·검증). SSH·run_job.sh·nohup 디텍치 패턴 동일.
- RF 재렌더/재캡션은 `scripts/recaption_slot.py`(RF=render_card use_brain=False 재트림+재캡션,
  AV=_recaption_av_preserve 렌더컷 재사용+캡션만). PD 방향은 `PD_RERENDER_DIRECTIVE` env로 주입.

## SHIPPED — 근본 2건 (커밋 5a20184)
1. **아기 레오 memory-lane 시점 캡션** (`C_timeframe`, 회고 §4.4). 근본: RF 캡셔너가 촬영일을
   받고도 프레이밍 가이드가 없어 몇 달 전 아기 레오를 "오늘"로, 기존 '그해→몇 년 전' 스크럽은
   **날짜를 안 보는 텍스트 함수**라 몇 달 전 footage에 "몇 년 전"을 박음(레오는 존재가 1년 미만이라
   거짓). Fix: `canon.timeframe_phrase`/`correct_timeframe_text`가 촬영일→경과월로 계산
   (~14mo 미만=지난 가을/겨울·몇 개월 전, 진짜 오래=N년 전). RF 캡셔너 writing-hint +
   번인 choke(KO) + AV `time_ago_phrase`(단일 소스)에 배선. caption_agent.md 시점 원칙 +
   Giri 캡('잘못된 시점') lockstep. 한 규칙이 두 펫에 다 맞음(랴니 2016=여전히 몇 년 전).
2. **AV 역할스왑 premise 금지 게이트** (`A25`, 회고 §4.2). 근본: 대조 두 펫 캐스트가
   '서로 바꿔보기'를 손쉬운 구조로 만들어 반복(8/22 역할반전·9/1 루틴체인지). 컨텐츠-dedup은
   공유 명사로만 비교해 단어 다른 같은 형태를 못 잡음. Fix: `producer._av_role_swap_hit` 결정론
   감지 → 1회 재작성(금지 디렉티브). writer_story '차이 자체가 엔진, 역할 교환 아님' +
   Giri 캡('역할스왑 재탕'). **다음 배치부터 발효**(기존 9/1 18:00은 재렌더 안 함).

## SHIPPED — 9/1 슬롯 3건 라이브 교체 (전부 Seedance 없이 저렴)
- **12:30 RF `xxtB-Qqb3zw`**: cut0 뒷모습 26s → **15~26s 트림(11s)** 고개 돌리는 payoff만 남김
  + 새 시점 캡션("지난 가을의 초록 벽에 기대던 레오 / 아기 레오"). 오늘·몇 년 전·원두 전부 없음.
- **21:00 RF `Dt3os9xSxT0`**: 시점 캡션 재생성("늦여름 빗소리에, **지난 봄** 러그 위 레오가 떠올라요").
  ⚠️ 촬영일 2026-03-06을 VLM이 '봄'으로 프레이밍(내 결정론 _SEASON_KO는 3월=겨울). PD는 "지난 겨울"
  언급 — '지난 봄'은 정직하고 규칙(계절/개월, not 년) 충족하나 PD 정확 워딩과 1단어 차이. **원하면
  '지난 겨울'로 1분 스냅 재번인 가능**(원본 클립 그대로).
- **18:00 AV `fthsQIKyCuk`**: PD "재생산 말고 캡션만 재밌게" → _recaption_av_preserve(5컷 렌더 그대로).
  펀치라인 강화("Peace Simulator v1.0 / 팬텀 캐치 / ERROR 404: tail not found / 레오가 또 출동 /
  위글은 랴니엄마가 해요"). 역할스왑 영상은 그대로(게이트는 다음 배치부터).
- 3편 라이브 예약 확인(12:30=03:30Z·18:00=09:00Z·21:00=12:00Z), 옛 3편(56wVR·1JWfD·dch_c) veto 확인.

## ⏳ 미완 — OAuth VM 전용 토큰 소유 (PD 수동 2단계 대기)
- 결정: **맥·VM 각자 별도 OAuth 클라이언트/토큰**(회전 충돌 근절, 맥 CLI 로컬 유튜브 유지).
  근본: 맥 CLI의 `list_scheduled_videos` 등 로컬 유튜브 읽기가 맥 토큰을 리프레시 → 공유 refresh_token
  회전으로 VM 무효화 위험(맥 token.json이 8/27 21:11 갱신된 게 그 흔적).
- 백업 완료: `youtube/client_secret.clientX.bak`·`token.clientX.bak`(gitignore됨). VM 무손.
- **PD님 남은 단계**: ① Google Console서 새 OAuth 데스크톱 클라이언트 생성→JSON 다운
  ② `mv ~/Downloads/client_secret_*.json youtube/client_secret.json` → 저 부르면
  ③ 맥 옛 token.json 제거 → ④ `! python -m youtube.oauth` 재인증 → ⑤ 검증.

## SHIPPED — PD 9/2 리뷰 (근본 2건 c2d65e2 + 슬롯 4건 정리)
- **0800 RF 옥상→마당 전체 재캡션** (`6i5-glkPzJc`, 옛 `KF8Gcazt49w` 교체). 처음엔 제목만 고쳤으나
  PD "언제 옥상이야 다 마당이야 재캡션" → payload의 옥상 전량 스크럽 + '마당' 지시로 재캡션 →
  캡션·제목·theme 전부 마당(옥상 0). 교훈: 잘못된 장소명은 제목만이 아니라 번인 캡션까지(footage가
  그렇게 안 보이면 pd 지시로 전 필드 스크럽 후 재캡션).
- **1800 RF 중복 2편 해결**: keeper=묘한평화협정 `cbiZSPO5u_Q`(재캡션='레오 펀치에 랴니 반응' 거짓 제거
  →'랴니 쿨쿨파워엔 미동 불가', 제목도 '레오가 뭘 하든 쿨쿨—묘한 평화'로 교정) · 카시트 `4baRydG9SIc` veto.
- **Durable A — 슬롯 충돌 가드** (`D_slotcollision`, 회고 §4.5). 근본: replace-not-add veto(bd12680)는
  *한 카드의 자기 옛 영상*만 회수 — **다른 두 카드가 같은 publish_at에 이중예약**(오프사이클 과잉생산)은
  못 막음. SKIP_FILLED는 생산시점만 보고 업로드 순간 재확인 안 함. Fix=`_auto_upload_episode` chokepoint서
  다른 uploaded 카드가 슬롯 차지 중이면 incumbent 유지+**skip(loud)**. 이 가드=9/2 사고 정확 예방.
  `SLOT_COLLISION_GUARD=0` revert.
- **Durable B — AV kick-부재 게이트** (`A26`, 회고 §4.2). 근본: '관찰카메라/시뮬레이터/분석 리포트' 가짜
  관찰-시스템 메타프레이밍은 장치가 코멘트만 하고 사건이 안 터져 kick 없음(9/2 관찰카메라='분석 실패'로 끝,
  9/1 평화시뮬레이터도 동류). Fix=`producer._av_kickless_meta_hit`(concept-frame 감지)→'실제 벌어지는 한 방'
  재작성 + writer_story kick 원칙 + Giri 캡('kick 부재'). `AV_KICKLESS_META_GATE=0` revert. 유닛테스트 통과.
- **1230 AV 관찰카메라** `r1i7h7k-H_4`는 PD대로 **오늘 건 그대로**(게이트는 다음 배치 발효).
- **9/2 최종 라인업**(슬롯당 1편, 카시트·옛묘한평화·옛0800 gone 확인): 08:00 RF `6i5-glkPzJc`(마당) ·
  12:30 AV `r1i7h7k-H_4`(관찰카메라) · 18:00 RF `cbiZSPO5u_Q`(묘한평화) · 21:00 AV `PQGNAsYVSV8`.

## ★ NEXT
- 다음 03:00 배치서 **durable 4건 첫 실전** 스팟체크: (8/31) RF 아기 레오 시점('지난 가을/겨울/몇 개월 전',
  오늘/몇 년 전 X)·AV 역할스왑 게이트 / (9/2) 슬롯 충돌 가드 발화([SLOT-COLLISION] 로그, 이중예약 0)·
  AV kick-부재 게이트(관찰/분석 컨셉 재작성, 과반려 X).
- OAuth PD 2단계 후 이어서 마무리.
- (선택) 21:00 RF '지난 봄'→'지난 겨울' 스냅 여부 PD 확정.

## gotcha
- `.env` 값 읽기는 하네스 시크릿 가드 차단(키 NAME만 grep 가능). VM에 로컬 스크립트 못 넘김 →
  `python -` stdin 파이프 or /tmp에 먼저 write. run_job.sh는 .py 파일 인자만(stdin X).
- recaption_slot AV 워크딧 선택 = `max(animated mp4 count)`(재시도 빈 dir 회피).
- _SEASON_KO 3월=겨울(결정론), VLM은 3월을 봄으로 볼 수 있음(캡션은 VLM 우선, 교정기는 년/그해만 스냅).
