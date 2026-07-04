# Session handoff — 2026-07-04

아주 긴 세션. 회고 문서 작성 → 7/4 배치 PD리뷰 전량 재제작 → 다수 durable 픽스(쿨다운/dedup/캡션 그라운딩/grandma 매핑). 아래 ★TODO가 다음 세션 진입점.

## ★ NEXT — 재정리된 TODO (우선순위 순)

1. **grandma 설명 자동매핑 forward 픽스** (task #5) — 백필은 끝났으나(아래 SHIPPED) **새 업로드는 여전히 pd_notes에 자동 저장 안 됨.** `slack/app.py`의 grandma 파일 인입/대화 경로가 "파일→봇질문→다음 메시지들(설명+맥락)"을 그 asset `pd_notes`(owner)에 쓰도록 수정. 이게 RF 내용정합의 근본.
2. **episode_stories 소재 큐레이션** (task #6) — 282행이 할머니 대화 원문 덤프. LLM으로 discrete 소재(트레잇·사건)로 추려내기+dedup+태깅. PD: "에피소드 소재들 추려내야."
3. **RF21 삼계탕 재make** (task #7) — `xX9EZEsxQjo`(7/5 21:00). pd_notes 이제 정확 → 97s 클립의 **먹는 구간**으로 트림, 레오 보이게, 사람다리 제외.
4. **AV18 렌더결함 진단+재렌더** (task #8) — `7poBXMKiwJ4`(7/5 18:00). 레오 꼬리 잘림/튜브 겉핥기/물리·인과 붕괴. 근본 진단 후 $50 재렌더.
5. **7/5 RF1230 재make** (task #9) — `VdaD-QH4WfU`(7/5 12:30) "집에서 각자 노는"=무사건 템플릿+6/29 재사용클립. 게이트는 이제 활성이나 예약분은 손 재make.
6. **AV21 v2 검증+전달** (task #11) — `VtjEL9fwkZA` 교체용 얼음-adore 렌더 **in-flight**(`data/tmp/av21_adore.log`, pid `/tmp/av21_pid`). 완료 시 프레임검증→reupload.
7. **durable 픽스 커밋 정리** (task #10) — 아래 SHIPPED 다수 미커밋. PD 승인 시 브랜치→main FF, `_*.py` 스크래치 제외.

## SHIPPED 이번 세션 (미커밋 — main 반영 대기)

### 파이프라인 근본 픽스
- **쿨다운: 한 번이라도 쓴 클립 all-time 제외** (`agents/producer.py`, `RF_USED_CLIP_ALLTIME`). 기존은 최근 4편만 → 6/29 클립이 창 밖으로 빠져 7/5 재사용됨. 이제 업로드된 모든 회차 클립 제외 + **fresh<6일 때만 재사용 폴백**(정말 없을 때만). 검증: fresh 1015/1120.
- **content_hash 인입 dedup** (`icloud/sync.py`+`slack/app.py`) — iCloud↔Slack·재업로드 중복이 별개 asset 되던 것 차단(md5). 백필로 로컬 **37 exact-dup 그룹·39 중복본** 수거(best_for=NULL). ⚠️near-dup(재인코딩)은 md5 못잡음=phash 2차 follow-up. GCS 전체 백필도 follow-up.
- **결정론 컨셉-재탕 게이트** (`_concept_lexical_collision`+RF `[재탕검증]`, main 97b1478) + **`[사건없음]` 무사건-공존 템플릿 게이트**(`RF_EVENTLESS_GATE`, 각자의방식/평행동행/따로또같이/각자노는 거부→재제안).
- **Giri 지어낸-이동/장면전환 cap** (reviewer CHECK0, main 97b1478) + **AV 캡션 그라운딩 생성기 픽스**(`caption_agent.md`+`writer_story.md`: 렌더 안 될 오프스크린 동작/장소 주장 금지, Giri와 lockstep).
- **grandma 설명 재매핑(백필)** (`scripts/_remap_grandma_desc.py`) — 채널 417메시지 훑어 **30클립 pd_notes에 설명+맥락 저장**(context-aware, 1:1 아님). owner 2/72→32. RF21 삼계탕 등 ground truth 확보.
- **토큰 원장 컬럼 승격** (`api_ledger.py`, tokens 컬럼+백필 60.6M).

### 7/4 배치 전량 재제작 (PD리뷰 → 4/4 교체·라이브)
- 08:00 RF `BBYun3os5tU` 계곡 물놀이 (카시트 3회 재탕 탈피, 베테랑 수영)
- 12:30 AV `ZV56FS-d17Q` 얼음나라 상상 (세대대결 재탕 탈피, 랴니 petite)
- 18:00 RF `dbm7GG4ZreA` 여름 나란히 발라당→꼬리 톡(손이 꼬리 건드림 실제내용, "무슨소리?" 오독 수정)
- 21:00 AV `VtjEL9fwkZA` 랴니의 산책 상상 (cut1 캡션 실내 정합) ← 단 PD가 "산책conceit 이상" 지적 → AV21 v2(#11) 재렌더 중
- 제목·DB theme 전부 내용 정합으로 수정(reupload는 옛 제목 남기므로 매번 수정 필요 — 알려진 갭).

### 회고 문서
- `notes/retrospective_2026-05_to_07.md` — 숫자/13시대 타임라인/에이전트 분화/실패·롤백 척추/통계 대시보드/토큰/현재 아키텍처/교훈. 슬라이드 포팅용.

## 이번 세션 교훈 (반복된 근본 패턴)
- **증상수정 ≠ 재발방지**: 에피소드 손수정만 하면 생성기가 다음 배치에 또 만든다. 프롬프트 조언만으론 LLM이 무시 → **결정론 게이트**로 박아야(쿨다운 all-time, [사건없음], content_hash, Giri cap 다 이 원리).
- **소스 진실은 owner 설명**: VLM 추측은 긴 클립의 엉뚱한 구간을 읽는다. 할머니 설명(pd_notes)이 ground truth — 매핑이 근본.
- **reuse는 정말 없을 때만, 최대 다양성**(PD 명시). 
- reupload는 옛 제목/메타 유지 → 교체마다 제목 수정 필요.

## 상시 주의
- 맥 네트워크 불안정=렌더 재시도 잦음(GCP 이관 별도 LOCKED). 커밋은 PD 요청 시. `_*.py` 스크래치는 main 제외.
