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

## 운영 루틴

### 매일

```
00:00 — iCloud sync (15분 주기 자동)
18:00 — Writer Agent 자동 실행 (launchd)
       → 다음날 카드 초안 생성, ask_pd=true 면 Slack 워크룸에 알림
~20:00 — PD가 Slack에서 /writer-show 확인 → /pd-approve 또는 /pd-reject
20:30 — Cameraman 렌더 (Phase 1에서 구현 예정, Phase 0는 수동 편집)
21:00 — 발행 (Phase 1 자동, Phase 0 수동)
```

### 슬래시 명령

| 명령 | 용도 |
|---|---|
| `/writer-run [date]` | Writer 즉시 실행 (기본=내일) |
| `/writer-show [date]` | 카드 요약 보기 |
| `/pd-approve <id>` | 승인 |
| `/pd-reject <id> <reason>` | 반려 |
| `/post <id>` | Cameraman 큐에 투입 |
| `/status` | 파이프라인 스냅샷 |

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
