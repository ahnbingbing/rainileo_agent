# Session handoff — 2026-08-24

**스파인:** 프레임이 ground truth. 리뷰 받으면 **재캡션 vs 재선택 vs 타깃-재렌더 vs 컷-제거** 판정. 이번 세션
큰 교훈 — **채널 사인오프처럼 '캡션으로 존재하는' 요소도 시각 제스처는 별도 레이어다**(캡션/태그 세팅 ≠ 화면 렌더;
렌더되는 건 motion_prompt). 그리고 **편집(무엇을 자르고 어떻게 프레이밍)은 footage가 좋아도 '사건이 화면에 보이게'
하는 별도 결정**이다(짧은 사건은 트림/루프, 가로 액션은 crop 아닌 letterbox).

## VM authoritative · push=deploy
- `git push origin main` → 배포 타이머 pull→smoke→봇 재기동. **VM HEAD = `eb21d09`**(코드), `85192e8`(회고).
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
- 수동 잡 = scp → `/tmp` → `sudo -u rianileo cp … scripts/` → `sudo -u rianileo bash deploy/run_job.sh scripts/_xxx.py`.

## SHIPPED — PD 8/25–8/26 리뷰 5건 (라이브 예약 교체, 프레임 검증)
- **8/25 12:30 AV** `PKAgNUmxw8s`→**`7h5X6muZmRk`** — cut3 유리식탁에 풀이 자람(=canon '레오 부추 먹음'을 히어로
  소품으로 무대화→Seedance가 잡초로 렌더) + cut5 윙크 랴니 드리프트. PD 승인 범위 **cut3+cut5만** 교정 스틸(nano-banana
  best-of-3) i2v 교체 + cut3 캡션서 '캣그라스' 제거. (cut2 부추 새싹은 승인 밖이라 유지 — PD 원하면 추가.)
- **8/25 21:00 AV** `Cfetg2kwNZg`→**`RcEti0tMnqI`** — 마지막 윙크 사라짐. 근본=클로저가 `function=wink_ending`+
  '햅삐' 캡션까지 있는데 **motion_prompt에 윙크 제스처가 없어** Seedance가 무-윙크 클로저 렌더. 클로저만 레오 윙크 스틸
  i2v 재렌더(~$0.6).
- **8/26 12:30 RF** `9_zQL12zm-s`→**`-i-gqMN3paQ`** — 옛 memory-lane 이불낮잠(이불→낙엽 오독 + 생리 기저귀를
  넥카라로 오독). 신선 **축구장 나들이**(med_2026_08_22, 8/22) 재선택.
- **8/26 18:00 AV** `AAc6NYrjrEI`→**`sEEE6E4unV4`** — cut2(갑자기 물리법칙 붕괴) 제거. recaption_finish에
  캡션 dict서 그 컷 키만 빼 4컷 재조립($0, Seedance 무).
- **8/26 21:00 RF** `C2ItRQ5sBI4`→**`PQUEY7utNQg`** — cut1(레오가 욕실서 랴니 등 급습) 살리고 cut2 제거. 급습이
  ~5s뿐이라 37s one-take가 희석(Giri 4)→ **0-10s로 트림 + blur-fill letterbox(등판이 가로라 crop이 잘라먹음) +
  2회 루프 인스턴트-리플레이**로 급습 강조.

## durable 코드 — `eb21d09`
1. **AV 윙크 제스처 결정론 주입**(`writer_director._enforce_wink_empty_captions`): AV 클로저 motion_prompt에 윙크
   문구가 없으면 wink_subject가 명확히 윙크하는 제스처를 주입(RF 면제·이미 있으면 중복 안 함). 유닛 3케이스 통과.
2. **canon 부추/캣그라스 렌더 경고**: 잎채소 간식은 AV 히어로 소품 금지(캡션 언급만; Seedance가 표면에 자라는 풀로
   렌더). 히어로 소품 = 청어·츄르·그릭요거트·치즈(접시/튜브, 깨끗 렌더). 츄르=치약튜브 가드와 동류.
3. **canon 랴니 생리 사실**: 생리기간=기저귀+멜빵(서스펜더)+나른('꽃도장'); VLM/캡션이 넥카라/옷으로 오독 금지.

회고 반영(`85192e8`): §4.2 **A23**(사인오프 캡션≠제스처 레이어)·**A24**(참 canon도 렌더특이성엔 짐), §4.4
**C_briefmoment**(짧은 사건 트림/루프+가로액션 letterbox)·**C_periodfact**(안 보이는 맥락은 canon으로).

## gotcha
- **AV 타깃 컷 재렌더** = 교정 스틸 생성(`gen_still_multiref.generate(bg, refs, prompt, out, KEY)`, best-of-N) →
  `animate_seedance_i2v.py --mode i2v --image STILL --prompt MOTION --fast --output animated/<tag>.mp4`로 스왑 →
  `recaption_finish`로 재번인·재조립(다른 컷 유지). 프리어 `_fix_av1230_*` 패턴.
- **컷 제거** = recaption_finish에 넘기는 캡션 dict에서 그 tag(+`_tempo_factors`)만 빼면 assemble가 그 컷을 뺀다.
- **가로 액션 clip** = 9:16 crop-fill(gotcha #11)이 가로로 걸친 상호작용을 프레임 밖으로 밀어냄 → blur-fill
  letterbox(pad, `scale=…decrease` fg + `boxblur` bg overlay)로 전체 프레임 보존.
- **fast i2v** 5s 캡(원래 7s 윙크컷이 5s로 줄어듦 — 캡션 타이밍 재조정 필요).
- `os.environ["GOOGLE_API_KEY"]`를 bash 힙독+print 명령에 넣으면 하네스 secret 가드가 막음 → 스크립트를 Write 툴로
  만들고 `.env`서 `os.getenv`로 읽어라.

## ★NEXT
- **다음 03:00 배치 durable 첫 실전 스팟체크**: AV 윙크가 실제로 화면에 렌더되는지(제스처 주입)·부추/캣그라스가 소품으로
  안 나오는지·생리 footage 맥락 반영.
- **미착수(선택)**: 8/25 12:30 AV cut2의 부추 새싹(PD 승인 밖이라 유지 — 원하면 cut2도 교정 스틸 재렌더).
- (이월) Giri confabulated-backstory 반려(B23) giri-update 미완.
