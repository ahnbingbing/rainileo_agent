# Session handoff — 2026-07-26

**스파인:** **파이프라인이 편집 결정을 하드코딩으로 뺏으면 스토리와 단절된 산출물이 나온다 — 프레임이
ground truth다.** 세 라이브 영상에 대한 PD 리뷰에서 출발했고, 파고들수록 겉보기 증상(빈약한 엔딩,
윙크만 통통, 캡션 오류, 소품 모핑)이 몇 개의 근본으로 수렴했다. "모든 컷은 실사인데 윙크만 이상하다"가
결정적 단서였다 — 그 컷의 생성 경로가 다른 것.

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `a43b729`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`
  (IAP 간헐 255 — 재시도). DB/스크립트는 `sudo -u rianileo`. YouTube 상태는 **라이브 API로 확인**.
- ★수동 재렌더/재업로드는 반드시 `deploy/run_job.sh <script.py>` 래퍼로(PYTHONPATH·시크릿·ffmpeg PATH).
  직접 `.venv/bin/python`은 burn 서브프로세스가 `ModuleNotFound: agents`로 죽는다.
- 인라인 heredoc 이스케이프 깨짐 반복 — 작은 `.py`를 `gcloud compute scp`로 올려 실행.

## SHIPPED (durable, 배포됨)

- **윙크 아키텍처 근본 (`0e4de6f`/`01fc31e`)** — 윙크는 스토리 결(closer) 비트에 **접힌다**(별도 append
  제네릭 컷 폐기). `_fold_wink_into_closer`가 마지막 스토리 컷을 wink_ending으로 만들고 payoff 펀치라인
  캡션(=마지막 캡션)+햅삐 사인오프 보존. `_enforce_wink_empty_captions`는 그 payoff를 안 지움(ai_vtuber만).
  **엔딩(상상에서 끝/현실복귀)은 Director가 스토리 보고 결정** — 현실복귀 강제(`_ensure_resolution_before_wink`)
  미호출 supersede. cameraman: 폴딩 윙크(`wink_folded`)는 AV_WINK_FRESH_STILL 재seed 건너뛰고 실제
  스토리 프레임서 체인 → 실거실·실사 몸. **이게 "빈약한 6번째 컷"과 "윙크만 통통/정원"의 공통 근본**
  (별도 컷이 자기만의 GPT 이상화 스틸에서 seed). A19(c1e589c fresh-still)는 근본 아닌 밴드에이드였음이
  드러남(회고 A20). 실렌더 검증 완료.
- **RF 청어 canon (`c0fd401`)** — 말린 청어 = **함미(할머니)가 장에서 사온 것**(하비가 말리는 게 아님)
  + **공동 간식은 둘 다 크레딧**(한 마리 '싹쓸이' 금지). canon.py + cameraman 캡션-VLM 블록 양쪽.
- **prop-drift 가드 phase 1 (`a43b729`)** — per-episode 상호작용 소품(터그)은 object_refs 앵커가 없어
  i2v가 매 컷 모핑(아령→공). Director가 `key_props`[{name,description}] 선언 + cameraman 결정론 PROP
  LOCK 주입 + Giri "소품 드리프트" cap≤6(가구 morph 사촌, AV 스코프). **phase 2(자동앵커) 미완**.

## 손수정 (라이브 3편 전부 교체, 프레임 검증)
- **#1 터그 챌린지** (was 9cdGYLgvgRc) → **wJm2IMwr2X8** 공개. 재렌더로 윙크 정원/통통 제거(실거실
  실사 랴니). ★prop drift(아령→공)는 이 재렌더엔 남아있을 수 있음(가드는 미래 배치 적용).
- **#2 간식 챌린지 RF** (was dldQ5Bes0G4) → **z7JDfJxKx7Y** 공개. $0 recaption_finish로 재캡션 교체:
  레오만먹음→둘 다, 하비청어→함미 장에서 사옴, 첫컷 "레오:" 라벨 제거. ★18:00에 이미 공개돼 있어 20시에
  교체(공개본 삭제→새 URL·조회 리셋).
- **#3 창틀 선수권** (was g6d7hgJIRZ4) → **gz7Zf4YWBIA**, 21:00 예약(=이 시각 공개됨). 폴딩 윙크본.
- ★공개본 재업로드 레시피: 공개 상태면 `upload_short(..., privacy="public")`로 즉시공개(기본 private
  이니 주의), 예약이면 reupload_episode.py(같은 publishAt). 둘 다 옛 vid veto(delete)+카드 갱신+bgm맵.

## ★NEXT
1. **다음 03:00 프로덕션 배치 스팟체크** — phase1 durable 첫 실전: (a) 윙크가 별도 컷 없이 스토리
   클로저에 접히는지 + Director가 엔딩(상상/현실) 고르는지, (b) key_props 선언한 컨셉의 소품 일관성 +
   Giri 소품드리프트 발화, (c) RF 청어 캡션이 함미/공동 반영.
2. **prop 가드 phase 2** — 자동앵커: 소품이 처음 렌더된 컷에서 canonical 크롭 추출→이미지 ref로 뒤
   컷 주입(A18 경로 확장). 프레임 내 소품 위치 탐지(VLM crop) 필요. PD가 "둘 다, 자동앵커 다음" 지시.
3. **#1 prop drift 잔존** — wJm2IMwr2X8은 윙크만 고쳐졌고 아령→공은 남아있을 수 있음. phase1/2 적용
   후 재렌더할지 PD 판단.

## 메모리
[[av_wink_folded_into_closer]] · [[av_prop_drift_guard]] · 회고 A20(4.2 AV 렌더 섹션).
