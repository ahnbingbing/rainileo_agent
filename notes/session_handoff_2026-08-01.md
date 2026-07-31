# Session handoff — 2026-08-01

**스파인:** 프레임이 ground truth. 생성기가 primary 방어, **결정론 backstop은 파이프라인 마지막 mutation 뒤**에
있어야 한다(앞단에만 두면 render-time VLM이 프레임만 보고 되돌린다). 캐릭터 트레잇 중 **나이-의존 값(크기)은
진화**하므로 "항상 X" 고정 canon이 드리프트를 *만든다*. AV 소품은 **원본 소스가 진실** — prop-lock 설명이
원본이 렌더하는 것과 달라도 fresh-still컷만 다른 물건을 그린다.

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `c866484`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
  DB/스크립트 `sudo -u rianileo`. YouTube 상태는 라이브 API로 확인.
- ★수동 작업은 `deploy/run_job.sh <script.py|-m module>` 래퍼(PYTHONPATH·시크릿·ffmpeg PATH).
- ⚠️ 파일을 `/home/rianileo/`로 직접 scp하면 권한 실패(내 유저=ahnbingbing) → **`/tmp`로 scp 후 `sudo -u rianileo cp`**.
- ⚠️ 렌더 workdir 타임스탬프가 출력 파일명과 **1초 어긋날 수 있다**(`_230745` workdir vs `_230746.mp4`) — recaption
  --workdir는 실제 `ls -dt data/tmp/cameraman_<card>_*`로 확인.
- 손수정 툴: `recaption_finish.py`(footage 보존 재번인+재조립, $0) → `reupload_episode.py --card <8자> --video <mp4> --title '..'`.
  카드 payload의 title/desc/draft를 먼저 원하는 값으로 UPDATE해야 reupload가 clean 텍스트를 씀(옛 제목 fallback 방지).

## SHIPPED — 8/1 PD리뷰 5건 (전부 라이브 교체 + 4 durable)
- **RF0800** → 카페 고양이 **원두** 무지개다리 이야기(레오 아님). `1zQ9W88kHF4`.
- **AV1230** → 막대기 drift 제거(장난감 일관) + **레오가 자기 장난감 독차지** 스토리 + 레오 크기 정상. `zipO8pfpyk4`.
- **RF1800** → **2017년 가을** 명시 + 알고보니 아기 랴니와 단짝 **원두**(캐리어 속) 이야기 + 길이 확보(27s, 여백 원본유지). `yDJPdXVXbF8`.
- **AV2100** → "더운날 가을 상상" hook + "**전매특허 트윈스 합체 포즈**"(상상속신호 대체) + 윙크 레오 크기 정상. `Ubp7xJytLY0`.
- **8/2 21:00** 빈 슬롯 = self-heal 6라운드 다 실패(asset_not_found→face-crop→gutted, 설계상 빈 채움)
  → 백로그 여름 수영장(Ryani 물마니아) 채움. `dve6Mji68vQ`. **8/2 = 4편**.

## durable (배포됨) — 상세는 회고 A21/A22/C20
- **RF 캡션 날짜 그라운딩(3cdf306)**: 원두 재발 근본. `_RF_ACTION_SYS`에 컷별 `[clip captured YYYY-MM-DD]`+시점규칙
  주입(생성기) + `_rf_existence_caption_gate`가 번인 직전 마지막으로 촬영시점에 없던 펫이름 결정론 치환(backstop) +
  producer 제목/설명 chokepoint도 동일. canon.WONDU 등재.
- **원두 과일반화 수정(ceb5966)**: backstop 기본을 중립("고양이")으로(원두 자동치환 폐기). 원두는 확정 클립에 캡션 직접
  명시(이미 존재하는 이름→backstop이 안 건드림). PD: "이 클립은 원두 맞지만 일반화는 말라".
- **레오 크기 canon 진화(58c4133)**: 레오 이제 ~10개월, 랴니와 비슷. canon 6곳+reviewer+character_sheets(S5)+
  director/producer/cameraman_validator 정합. 현재컷=comparable, smaller는 명시적 아기/과거 컷만. reviewer도 역방향.
- **Rhyani 오탈자 교정기(bfff005)**: `_CANON_NAME_FIX`에 Rhyani/Rihani 등 추가.

## ★NEXT
- **다음 03:00 프로덕션 배치에서 durable 첫 실전 스팟체크**: ①pre-Leo 고양이 캡션이 '레오' 아니라 중립('고양이')으로
  나오는지(원두 자동명명 안 하는지) ②AV 현재컷 레오가 랴니와 comparable 크기(아기레오 재발 X)·과거컷만 smaller
  ③AV 소품 일관성(윙크컷 포함) ④Giri가 comparable 레오를 결함으로 오반려 안 하는지.
- 원두를 arc/컨텐츠 소재로 확장할지는 PD 결정(pre-Leo=원두 일반화 금지 — 확정 클립만).
- 미해결(이전 세션 이월): iCloud 싱크 수렴버그(truncated 자산 무한루프), RF 여름 footage 얇음.
