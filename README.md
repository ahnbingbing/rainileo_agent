# Ryani & Leo Agent — Phase 0

YouTube Shorts 자동화 시스템 v1.3 / Writer Agent v1.1.
오늘(2026-05-09) 안에 Phase 0 셋업 완료, 내일(2026-05-10) 21:00 KST 첫 영상 발행이 목표.

## 디렉토리

```
rianileo-agent/
├── .env.example              # 환경변수 템플릿
├── requirements.txt
├── db/
│   ├── schema.sql            # SQLite DDL (milestones, assets, cards, runs ...)
│   └── init_db.py            # 초기화 + 시드 로딩
├── data/
│   ├── milestones_seed.json  # 기념일 시드 (Ryani/Leo 확정 날짜)
│   ├── concept_card_schema.json   # Concept Card v2 스키마
│   ├── concept_card_examples.json # 4건 예시 (Daily Warm/Fun/Trends + Memory Lane)
│   └── concept_card_2026_05_10.json # 첫 영상 카드 (사전 작성)
├── prompts/
│   └── writer_system.md      # Writer Agent 시스템 프롬프트 (v1.1)
├── agents/
│   └── writer.py             # Writer Agent 실행기
├── slack/
│   └── app.py                # Slack Bolt (Socket Mode) 워크룸
├── youtube/
│   ├── oauth.py              # OAuth 부트스트랩
│   └── upload.py             # 업로드 헬퍼
├── icloud/
│   └── sync.py               # macOS Photos → assets 테이블 동기화
├── launchd/
│   ├── com.rianileo.writer.plist        # 매일 18:00 KST Writer
│   ├── com.rianileo.icloud-sync.plist   # 15분마다 동기화
│   └── com.rianileo.slack.plist         # 워크룸 상시 구동
└── tests/
```

## 셋업 순서 (오늘, 시간 단위)

### 1. Python 환경 (10분)

```bash
cd ~/code/rianileo-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 선택: macOS Photos 직접 읽기
pip install osxphotos
```

### 2. 환경변수 (5분)

```bash
cp .env.example .env
# 편집: ANTHROPIC_API_KEY, SLACK_*, YOUTUBE_*, ICLOUD_INBOX
```

### 3. SQLite 부트스트랩 (1분)

```bash
python -m db.init_db
# [ok] tables created: 9
# [ok] subjects seeded: 2  (Ryani, Leo)
# [ok] milestones seeded: 5  (children_day, ryani_birthday, leo_birthday, leo_adoption_anniversary, channel_anniversary)
```

### 4. Slack 앱 (20분)

api.slack.com/apps 에서 새 앱 생성:
- Socket Mode 활성화 → App-Level Token (`xapp-...`) 발급, scope=`connections:write`
- OAuth & Permissions → Bot Token Scopes: `chat:write`, `commands`, `files:read`
- Slash Commands 5개 등록: `/writer-run`, `/writer-show`, `/pd-approve`, `/pd-reject`, `/post`, `/status`
- 워크스페이스에 설치 → Bot User OAuth Token (`xoxb-...`)
- `.env`의 `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/`SLACK_SIGNING_SECRET`/`SLACK_WORKROOM_CHANNEL` 채우기

```bash
python -m slack.app   # 콘솔에 "starting" 보이면 OK, Slack에서 /status 눌러 확인
```

### 5. YouTube OAuth (15분)

- console.cloud.google.com → 새 프로젝트 → API 활성화: YouTube Data API v3, YouTube Analytics API
- Credentials → OAuth client ID (Desktop app) → JSON 다운로드 → `youtube/client_secret.json` 저장
```bash
python -m youtube.oauth
# 브라우저에서 채널 계정으로 로그인, 동의 → token.json 저장
# [ok] authorized as channel: ...
```

### 6. iCloud / Photos 연결 (10분)

옵션 A — osxphotos 직접 (권장): 이미 `pip install osxphotos` 했다면 추가 작업 없음.
옵션 B — 워치폴더: `.env`의 `ICLOUD_INBOX`를 본인의 공유 앨범 export 폴더로 지정. 폴더 안에 새 파일을 떨어뜨리면 자동 인입.

```bash
python -m icloud.sync   # 첫 동기화 — 시간 좀 걸림. assets 테이블 채워짐
```

### 7. launchd 설치 (5분)

```bash
# plist 안의 /Users/ligi/code/rianileo-agent 경로를 본인 경로로 수정 후
cp launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.rianileo.slack.plist
launchctl load ~/Library/LaunchAgents/com.rianileo.icloud-sync.plist
launchctl load ~/Library/LaunchAgents/com.rianileo.writer.plist
```

### 8. 첫 카드 검증 (1분)

`data/concept_card_2026_05_10.json`이 사전 작성되어 있음 (memory_lane / side_by_side, 채널 첫 영상 자기소개). Writer Agent 거치지 않고 바로 PD 검토 → 영상 편집 가능.

```bash
python -c "
import json
from jsonschema import Draft7Validator
schema = json.load(open('data/concept_card_schema.json'))
card = json.load(open('data/concept_card_2026_05_10.json'))
errs = list(Draft7Validator(schema).iter_errors(card))
print('PASS' if not errs else errs)
"
# PASS
```

## 운영 루틴 — 런칭 자동화 (PD 2026-06-07)

첫 달은 **explore 전용 4편/day A/B 실험**. 자세한 설계는 `notes/first_month_plan.md`.

```
매일 00:00 — launchd(com.rianileo.launch): 다음날치 4편 제작
            (2 av + 2 rf, 레인×시각 라틴스퀘어), 기리 통과분만
            다음날 슬롯(08:00/12:30/18:00/21:00)에 예약공개,
            4편 + 모르는 점 질문을 Slack 워크룸 스레드에 게시
그날 하루 — PD가 Slack에서 검토: /veto(거르기), /answer(질문 답변)
다음날 슬롯 — (veto 안 된) 영상 공개
06:30 — bandit-collect (48h 성과 수집)
월 10:00 — bandit-report (av vs rf 누적 리포트 → Slack)
06:00 — iCloud sync + VLM 태깅 + 원본 정리
15분 주기 — Slack #background/#episode/#photos → DB
일시정지: launchctl unload ~/Library/LaunchAgents/com.rianileo.launch.plist
```

### 컨셉 디렉티브 우선순위 (그날 무엇을 만들지 결정 — `agents/arc.py:next_directive`)

```
1. PD /concept <날짜> <내용>   ← 그 날짜에 지정했을 때만. 무조건 최우선
                                  (ARC_ENABLED 꺼져 있어도 작동)
       ↓ 없으면
2. 런칭 인트로 오버레이          ← 1주차 Day1(둘 함께 자기소개)/Day2(레오 단독)/
   (LAUNCH_START_DATE 기준)        Day3(랴니 단독). 결정적(LLM 환각 방지)
       ↓ 1주차 아니면
3. arc 시즌 플랜 디렉티브        ← 롤링 ~1개월 계획(계절/공휴일/트렌드/월1 재소개),
                                  실제 클립 인벤토리 + CHARACTER_FACTS 그라운딩
```
즉 **`/concept` 안 쓴 날은 전부 arc가 기본**으로 굴린다. 캐릭터 사실/자산 충실도는 모든 층에서 항상 강제.

### 슬래시 명령

| 명령 | 용도 |
|---|---|
| **`/launch [date|dry|noupload]`** | 그날 4슬롯 제작·예약공개 (dry=배정만, noupload=렌더만) |
| **`/concept [YYYY-MM-DD] [내용]`** | 특정 날짜 컨셉 예약(최우선). 인자 없으면 목록 |
| **`/veto <card|video_id> [delete]`** | 자동발행 회차 내림(기본 비공개, delete=삭제) |
| **`/bandit [collect|choose]`** | av vs rf A/B 현황 / 다음달 레인·시각 추천 |
| **`/knowledge`** | 컨셉이 PD에게 물은 '모르는 사실' 대기열 + 저장된 사실 |
| **`/answer <id> <답변>`** | 그 질문에 답 저장(영구, 다음 컨셉부터 반영) |
| `/daily [date|dry]` | (구) 일일 제안→PD컨펌→제작 파이프라인 |
| `/test [rf|ai]` | 컨펌 없이 1편 진단 렌더 (결과 영상 스레드 게시) |
| `/upload <id>` | 렌더된 카드 수동 YouTube 업로드 |
| `/sync` | iCloud 수동 동기화 |
| `/writer_run [date]` / `/writer_show [date]` | Writer 직접 실행 / 카드 조회 |
| `/pd_approve <id>` / `/pd_reject <id> <reason>` | 승인 / 반려 |
| `/post <id>` | 렌더 큐 투입 |
| `/bot_status` | 파이프라인 스냅샷 |

전체 사용법: `notes/slack_commands_guide.txt` (채널 핀 고정용).

## 등록된 1회성 알람 (Cowork Scheduled Tasks)

| Fire 일시 (KST) | 이벤트 |
|---|---|
| 2026-09-18 10:00 | Leo 첫 생일 + 추석 더블 콜리전 D-7 |
| 2026-11-08 10:00 | Leo 입양 1주년 D-7 |
| 2027-04-05 09:00 | **PRIME** imagined_together 일러스트 발주 D-30 |
| 2027-05-03 10:00 | 채널 1주년 D-7 |

## 주의

- `.env`는 절대 커밋 금지 (`.gitignore`에 포함됨)
- `YOUTUBE_DEFAULT_PRIVACY=private`로 시작 → 첫 카드 PD 컨펌 후 수동으로 public 전환
- Memory Lane `imagined_together`는 정책상 PD 승인 + 일러스트(수채화/연필 등) + 공시 문구 3종 모두 통과해야 발행
- AI Ryani/Leo 포토리얼은 절대 금지 (스키마 단계에서 차단됨)

## 검증 결과

| 항목 | 상태 |
|---|---|
| SQLite 스키마 + 시드 로딩 | ✅ 9 tables, 2 subjects, 5 milestones |
| Concept Card v2 스키마 | ✅ Draft7Validator |
| 4개 예시 카드 (Warm/Fun/Trends/Memory Lane) | ✅ |
| 2026-05-10 첫 카드 | ✅ (memory_lane/side_by_side, ask_pd=true) |
| 4건 1회성 스케줄 알람 | ✅ |
