# Session handoff — 2026-07-13 (7/10→7/13 대형 세션: 검수 게이트 구멍 대량 봉합)

스파인: **"사람이 쉬어도 버티게 게이트가 받쳐줘야 한다."** PD가 며칠 수동 리뷰를 쉬자 나쁜 영상이
무더기로 나갔다 — 개별 증상이 아니라 **검수 게이트 구멍**이 근본. 이번 세션은 그 구멍들을 하나씩
**결정론**으로 막았다. 교훈: semantic 프롬프트 룰은 존재만으론 러버스탬프된다 → 검수기가 별도 필드로
또렷이 답하게 강제 + 그 답에 결정론 cap. VM HEAD = **e615160**. 다 배포·라이브.

## VM authoritative. push=deploy.
`git push origin main` → deploy 타이머(2분 폴)가 pull→smoke→봇 재기동. VM 렌더 실행:
`sudo -u rianileo bash -c 'set -a; source /etc/rianileo/env; set +a; PATH=/home/rianileo/.local/bin:$PATH
PYTHONPATH=/home/rianileo/rianileo-agent /home/rianileo/rianileo-agent/.venv/bin/python ...'`.
배치 로그 = `data/logs/cron.launch.log`(옛 launch.out/batch_problems 아님 — board 로그툴 이제 여기 봄).
**data/(set_library.json·scene_refs)는 .gitignore → git 아니라 VM에 직접 유지**(deploy reset이 무시).

## 1. 재발방지 게이트 (전부 배포 — 이 세션 핵심)
- **canon 나이 자동교정**(742f5fc): "N살 차이"→"10살"(랴니2015·레오2025). burn chokepoint, 양 레인.
- **prune 7일 age 보존**(9a801ef): 최신6개 카운트→7일 age. 재-번인 소스 유지($50 강요 방지). `CAMERAMAN_TMP_KEEP_DAYS`.
- **종↔이름 스왑 감지**(30b56ba): 둘 다 보여도 이름이 반대 동물(주황고양이="랴니")이면 그라운딩이 `subject_swapped`로 잡음.
- **완성/데코 소스 감지**(c90a299): 소스에 이미 텍스트·스티커·하트 박혔으면(`baked_text`) 컷 드롭, 완성영상이면 슬롯 실패→raw 재선택. `RF_FINISHED_SOURCE_GATE`.
- **무-스토리 결정론 cap**(9c472fa): 검수기가 `story_arc_present`를 **명시적으로 답하게** 강제 → AV false면 자동 cap5. 추상무드("거실이 커진다면") 차단.
- **얼굴게이트 완화**(101f2dd): 구조붕괴(melt/orb)는 단독 차단, 물렁 smear는 홀리스틱도 "업로드" 아닐 때만(두 신호 합의). 자는/발라당 포즈 오탐→$50 폐기 방지.
- **GCS 업로드 회복력**(51dc65c): 타임아웃600+resumable청킹+재시도. 불안정 맥망서 큰 클립 스택 방지.
- **reupload 제목버그**(a408694): remake 교체가 옛 카드 제목 쓰던 것 → 영상 render_meta에서 새 제목 자동 도출 + `--title`.
- **recaption assemble 재시도**(e615160): 일시 ffmpeg 실패로 캡션수정 깨지던 것 3회 재시도.

## 2. AV 배경·소재 결합 (aa7884e, [[av_grandma_binding_and_set_anchor]])
- **set_anchor 결정론 backfill**: AV가 set_anchor 비면 GPT 일반방 폴백→실집 드리프트. cut space 최빈값(없으면 home_livingroom) 강제.
- **grandmompapa_recent 필드**: `[요청]/[컨셉]`만 최신순 별도 노출 → AV Writer 프롬프트 최상위 hoist + writer_story spine 강제(PD /concept 아래). RF는 content_desc로 이미 결합.
- **scene_ref_extras**: 0개였음 → 실내 grandma 클립에서 VLM 필터로 깨끗한 거실 프레임 3장 추출·등록(PD "reference는 영상에 넘쳐"). VM `data/set_library.json` + `assets/scene_refs/extras/`.
- **RF de-haze**: 할머니 렌즈 얼룩→소프트 footage. 컷별 lapVar(blur) 측정, 소프트밴드(40~800)만 unsharp+약대비. `RF_DEHAZE`.

## 3. board 봇 = 이제 자립 (D24)
- 로그툴이 죽은 파일명(launch.out) 봐서 "왜 실패" 못 짚던 것 → cron.launch.log 연결.
- **executor claude 바이너리 VM 설치**(apt node18 + `@anthropic-ai/claude-code` → /usr/local/bin, ~/.local/bin 심링크, .claude.json hasTrustDialogAccepted, ANTHROPIC_API_KEY 유지). 이제 board가 "왜 실패"를 스스로 분석·수정(코드/프롬프트). 렌더/업로드는 설계상 여전히 CLI.

## 4. 영상 수술 (7/12~7/13 배치)
- 7/12 대참사 5건 근본수정 후 예약본 4편 재제작 교체(손실0): AV2100 몸바꾸기(+cut4 물매니아 canon수정), AV0800 에어컨, RF1230 낮잠, AV2100 recap. AV1800 꿈(제가 얼굴게이트 오탐서 살린 렌더)=PD "매우 훌륭".
- 7/12 저녁 캡션 2건: **RF2100** 물그릇→**함미아비 설거지+토마토국물 핥기**(PD스토리), **RF1230** "레오 비틀"→"배 까고 그대로"(정지 일치).
- **7/13 최종 예약(라이브 확인)**: 오늘21시 MzK7(몸바꾸기,공개됨)/내일 08:00 mG43(에어컨)·12:30 4BdvL5(낮잠)·18:00 MiKO(꿈)·21:00 yq5y4B1JA3Q(설거지).
- ⚠️ **공개본 2편은 그대로**(human-in-loop 한계, PD결정): RF2100 6p1LOW08nPU(랴니↔레오 스왑)·XBeEe1saTwk(완성영상 위 캡션도배). 이미 공개라 고치면 조회수 손실.

## ★NEXT
1. **★7/13 03:00 배치 스팟체크 (지금)** — 5개 새 게이트 첫 실전. 과발화 감시: ①무-스토리 cap이 좋은 AV를 story_arc=false로 오판→AV 재렌더($$)? ②finished-source가 grandma 데코클립 과드롭→슬롯 비움? ③디스크(prune7일)는 34G여유라 안전. 다 fail-safe(VLM필드 없으면 무동작)이나 첫판 확인 필요. `cron.launch.log`.
2. 7/12 workroom 자동진단은 여전히 없는 파일명 지어냄 — board가 이제 claude로 실검증 가능하니 개선 관찰.
3. scene_ref_extras 3장뿐 — 더 다양한 각도 추가 여지(실내 클립서 추출).
4. recaption_finish assemble 일시글리치=재시도로 완화했으나 근본(부하시 ffmpeg 실패) 관찰.

교훈(회고 A16·B11·B12·B13·C10·C11·D24·D25에 상세): **검수기도 파이프라인이다**(fallback/재시도), **소재는 흐르기만 해선 안 쓰인다**(구분·우선·강제), **완성영상 위엔 캡션 안 얹는다**, **공개된 건 못 되돌린다(예약단계가 전부)**, **self-heal/봇 진단은 코드로 검증 전엔 믿지 마라**.
