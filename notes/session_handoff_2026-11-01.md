# Session handoff — 2026-11-01 (오리지널 MV 대량 세션: 댄스·파티·발라드 + MV 파이프라인 구축)

**스파인:** PD의 "오리지널 뮤비" 방향을 실제 제작으로 밀어붙인 초대형 세션. 우리 곡(Suno) + 랴니레오
실제 스토리 가사 → AV/실사 MV. 재사용 파이프라인(`scripts/make_mv.py`)과 Gemini 비디오 레이어
(`scripts/video_actions.py`)를 세우고, 댄스·파티·발라드 3편을 PD의 촘촘한 검수 루프로 다듬음.
★이 세션의 교훈 다수가 메모리에 반영됨 — [[mv-pipeline-and-action-layer]] [[mv-agent-spec]]
[[party-mv-rihanna-spec]] [[original-mv-direction]] [[mv-caption-sync]].

## 예약/발행된 MV (YouTube)
- **워밍 스토리 "랴니와 레오"(실사)** — `SLq5cgXqBWM` · 10/30 21:00 (AV판 794pEdpKiDc veto→비공개).
- **댄스 "놀자 놀자"(AV)** — `stihiIazI3E` · 10/31 21:00 · Short(49s).
- **파티 "간식 파티" Twista(AV)** — `_e_JTI5Fv4Y` · 11/1 21:00 · 롱폼(84s).
- **발라드 "나란히" A2 무의상(실사+AV)** — `LL86U719sFs` · 11/2 20:00 KST (21:00 회피).
  (의상 버전=ryani_leo_ballad_a2_outfit.mp4 별도 저장, 랴니 드레스 이상 → 테스트.)
- ★**예약 시간 주의: 21:00은 런칭 RF 슬롯(18:00·21:00)과 겹침** → MV는 20:00 등 다른 시간으로.

## 구축한 재사용 인프라 (durable, 미커밋 — scripts/는 로컬)
- **`scripts/make_mv.py`** — MV 조립 파이프라인. clips=[{src,dur,ss,fill}] + lines(카라오케) +
  title/credit(기본 "작곡 라레수·작사 함미하비") + outro_hold. HLG→SDR 톤맵+다크그레이딩, CFR, 범퍼
  맨끝(자기오디오 제거, 노래가 범퍼까지), 뮤비 타이틀카드(핑크 키커+악센트), 연속중복 캡션 병합.
- **`scripts/video_actions.py`** — Gemini 비디오. `actions()`=액션+타임스탬프(프레임샘플 VLM이 놓치는
  1-2s 액션), `align_audio()`=카라오케 정렬(Suno word-align보다 정확; 긴곡은 반 잘라 청크).
- **`scripts/gen_song.py`** — Suno 곡 생성(--all-clips 여러버전, 아카이브).
- **빌드 스크립트**(data/tmp/mv/): build_danceav.py, build_party_tw2.py, build_ballad.py,
  render_perf*.sh(연주컷), render_party_twista2.sh(디렉팅 반영).

## 다음에 만들 버전 = ★다른 주제의 새 곡들 (주제 우선순위대로)
각 주제마다 곡 생성(여러 스타일 버전 → PD pick) → MV. [[mv-agent-spec]] 주제 리스트:
1. **같이 자는 유대** — A2 완료(발행). **C2(로파이 포크)** 마저 만들면 이 주제 끝.
2. **엄마 기다리기** — 엄마 소리에 랴니 귀 쫑긋 눈 반짝 vs 시크한 레오 그루밍. 감성 발라드/미드.
3. **할로윈** — 둘이 날개 달고 "인기최고". 타임리·밈 AV(훅).
4. **소파 사수 전쟁** — "까불지마" 으르렁 랴니 vs 약올리는 레오. 업템포·밈.
→ 함미하비 스토리에서 더 발굴 가능(pd_notes). 장르별 레인: 발라드=실사/RF·다크, 댄스/힙합=밈 AV.
(부수: 파티 리안나 Umbrella판 미완·놀자놀자 실사판·랴니 드레스 개선 — 급하지 않음.)

## ★할일 = MV 에이전트 (지금 세션의 결론)
- **MV 자동생성 에이전트 착수** [[mv-agent-spec]] — PD 핵심. 포맷은 이번에 고정됨(발라드 RF 하이브리드
  + Twista/댄스 AV). 스테이지: **주제 에이전트**(함미하비 스토리 발굴·선택) → **곡 에이전트**(가사→
  Suno 여러버전→PD pick, 청아보컬·짧은인트로긴아웃트로) → **MV Writer/Director**(레퍼런스 디렉팅
  분석·가사 의성어 살리기·컷수 늘려 타이트매핑·장르레인) → 렌더 → make_mv → **검수 에이전트**(PD
  검수방식 복제: 전체컷 contact-sheet로 서브젝트/행동/장소/색/바우위치 검증·Gemini 자동체크).
  이걸 만들면 주제별 MV를 반자동으로 찍어낸다.
- **부속 할일**(에이전트 작업에 흡수): Gemini 액션레이어를 프로덕션 파이프라인에도 적용(PD "해야만해")
  · make_mv/video_actions git 커밋 · 립싱크(보류, PD 결정 대기) · 버전은 항상 별도 파일명.

## 핵심 검수/제작 교훈 (반복 실수 방지)
- **전달 전 전체 컷 contact-sheet 검수** — 스팟체크는 결함 놓침(채도·안보이는펫·기타작음·바우위치).
- **서브젝트/행동/장소 매칭** — 가사 "소파 랴니"면 랴니가 소파에(바닥X), "눕는 레오"면 차분히 누운
  레오(노는것X). VLM 라벨 믿지말고 Gemini/프레임 확인.
- **SDR 클립이 다크그레이딩 우회** — "아직도 밝아"의 진짜 원인. color_transfer 확인, SDR→HLG swap.
- **Seedance "둘이 무대/정면 바우" = 밴드멤버(다수동물) 추가** → 솔로 번갈아 + 나란히(마이크/기타)
  구도만. 바우는 솔로 or 나란히 구도.
- **긴 곡 align은 반 청크** · **원곡 > 트림/스플라이스/재생성**(PD "길면 뭐 어때").
- **Suno: 짧은인트로·긴아웃트로 프롬프트** · **여성보컬=청아(보아느낌)** · **여러버전 비교→PD pick**.
- **레퍼런스 구동**: PD가 유튜브/share.google 링크 주면 yt-dlp+Gemini(영상) / WebFetch(의상)로 분석.

## NEXT (다음 세션)
발라드 A2 무의상 11/2 예약 확인 → **C2 제작**(동일 검증 플랜) → 랴니 드레스 개선 → **MV 에이전트 착수**
(포맷 고정됐으니) + **프로덕션 파이프라인 Gemini 적용**. 립싱크는 PD 결정 따라.
