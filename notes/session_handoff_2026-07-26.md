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

### ★★1. AV 배경 연속성 — 이전 컷 프레임 기반 순차 스틸 regen (PD 최우선, 미착수)
**증상(PD 7/26):** "이번에 배경이 다 흔들려." #1/#3 재렌더에서 story 컷의 방이 컷마다 달라짐.
**근본:** 단일공간 AV의 story 컷은 `ref` 모드로 **각 컷이 자기 방을 독립 생성**한다(이전 컷을 input에
안 받음). 현재 기본은 "per-cut gpt-image 스틸 → i2v"인데, 그 스틸을 뜨는 `generate_scene`이
`images.edit(reference_image, prompt)`에서 **reference_image = 정적 캐릭터 base ref**(고정 펫)를 써서
이전 컷의 방을 이어받지 못한다 → 배경 독립생성 → 흔들림. (부차: `home_livingroom_clean.png` 등
scene_ref가 `.gitignore *.png`로 **git·VM에 부재**(로컬만 존재)→GPT 폴백. char-ref는 9e69300로 편입했으나
scene_ref는 안 됨. 단 PD는 이게 아니라 아래 chain이 핵심이라고 명확히 함.)

**PD가 원하는 메커니즘(확정):** **순차 렌더** — cut N의 스틸을 **이전 컷의 렌더된 마지막 프레임을
base로 gpt-image regen(best-of-5 픽)** → 그 스틸(이전 방을 이어받음)을 i2v input으로. 배경은 이어지고
캐릭터는 매 컷 프롬프트+canon으로 **fresh 재생성**되니 i2v-체인 몽타주 붕괴([[av_chain_only_continuous]])는
없다(스틸이 새로 그려지므로 누적 드리프트 X).

**★★필수 제약(이전 실패 포인트):** regen된 스틸이 **2D/평면·원근 깨짐이 나면 안 된다.** 이게
AV_PRECISE_STILL이 default OFF된 이유(cameraman.py:7194~7197 "원근 다 무시, 2d 느낌… 파이프라인
망가졌어" → per-cut gpt 스틸+i2v로 롤백하며 배경흔들림을 감수했었음). 실사 프레임을 edit하면 실사가
유지되지만 프롬프트로 photoreal/자연 원근 강제 + **2D 거부 VLM 가드**(`_scene_ref_is_clean` 식)로
반드시 검증. 실사감 3사배심 A 만장일치 기준([[av_firstlast_interp_rejected]]).

**재사용 인프라:** `_av_still_compose_prompt`(cameraman.py:246 — "Use the PROVIDED ROOM PHOTO as the
EXACT background, keep pixel-identical" + CAST-explicit)와 `_compose_av_still`(218)이 **이미 "제공된 방
사진으로 스틸 합성"을 한다**. 지금은 방 사진=정적 clean scene_ref(AV_PRECISE_STILL 경로). → **방 사진을
이전 컷의 렌더 프레임으로 바꾸고 + 2D 가드 추가**가 핵심 변경. chain 프레임 추출은 이미 있음(7971-7982
`chain_jpg`). 모드 라우팅/precise 분기: 7202(_av_precise 플래그)·7319-7350(mode ref↔i2v, `_precise_tags`).
**구조 변경:** 현재 "batch 스틸생성(generate_batch 7250)→전체 렌더 루프(7900+)"라 이전 *렌더* 프레임을
쓰려면 **컷별 순차(렌더→프레임추출→다음 스틸 compose→렌더)**로 interleave해야 함. (차선: 이전 *스틸*
체인으로 근사 — 덜 침습적이나 PD는 "프레임" 명시.) 코어 렌더 경로라 반드시 실렌더로 배경연속+무2D+무붕괴
검증 후 배포.

### 2. prop 가드 phase 2 — 자동앵커 (PD: "구축은 하되 #1 재렌더는 불필요, 이미 공개")
소품이 처음 렌더된 컷에서 canonical 크롭 추출→이미지 ref로 뒤 컷 주입(A18 `object_refs` 경로 확장).
프레임 내 소품 위치 탐지(VLM crop) 필요. phase1(key_props+PROP LOCK+Giri, a43b729)은 배포됨.
**#1(wJm2IMwr2X8) prop 잔존(로프→뾰족공 확인)은 이미 공개돼 그대로 둠** — 재렌더 안 함.

### 3. 다음 03:00 프로덕션 배치 스팟체크
phase1 durable 첫 실전: (a) 윙크가 별도 컷 없이 스토리 클로저에 접히는지 + Director가 엔딩(상상/현실)
고르는지, (b) key_props 선언 컨셉의 소품 일관성 + Giri 소품드리프트 발화, (c) RF 청어 캡션 함미/공동 반영.

## 메모리
[[av_wink_folded_into_closer]] · [[av_prop_drift_guard]] · 회고 A20(4.2 AV 렌더 섹션).
