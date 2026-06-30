# deploy/ — VM 두뇌 배포 (P1)

[gcp_migration_plan.md](../notes/gcp_migration_plan.md)의 P1 산출물. **"git push to `main` = 배포"**
를 구현한다. VM(GCE e2-medium @ 서울)이 항상 켜진 두뇌(봇·cron·렌더·DB), Mac은 새벽
osxphotos 델타만. 봇을 데이터센터로 옮기는 것 자체가 원래 BrokenPipe의 근본 해결.

## 파일

| 파일 | 역할 |
|---|---|
| `bootstrap.sh` | 1회 VM 프로비저닝(패키지·폰트·유저·clone·venv·시크릿·유닛·crontab). 멱등 |
| `config.env.example` | 배포 설정(비밀 아님) → `/etc/rianileo/deploy.env` |
| `pull_deploy.sh` | main 폴링 → 격리 clone에서 smoke → 통과 시 라이브 전진 + 봇 재시작 |
| `smoke.sh` | 배포 게이트: py_compile 전수 + 핵심 모듈 import. 실패 시 배포 차단 |
| `run_job.sh` | cron 잡 공통 래퍼(시크릿 source + cwd/PYTHONPATH/TZ) |
| `crontab.vm` | 주기 잡 1:1 포팅(KST) |
| `systemd/rianileo-bot.service` | 상시 봇(Restart=always) = BrokenPipe 해결 |
| `systemd/rianileo-deploy.{service,timer}` | 2분마다 배포 폴링 |

## 배포 모델 (PD: "git push만으로 ok?")

```
push → main ── (≤2분) ──▶ VM rianileo-deploy.timer
                            └ git fetch; main 이동 시:
                               격리 clone에서 smoke.sh
                               ├ PASS → 라이브 git reset --hard + (req 바뀌면)pip + 봇 재시작
                               └ FAIL → 차단(라이브는 직전 양호본 유지) + Slack 경보
```
- **깨진 push는 봇을 죽이지 않는다** — smoke가 격리 clone에서 먼저 잡는다.
- **cron/timer 잡**(launch/producer/board…)은 매 발화 시 새 python이라 자동으로 새 코드.
  **봇만** 장수 프로세스라 재시작 필요.
- board executor·CLI·모든 세션이 이제 `main`으로 push(활성 브랜치 전환 완료) → 자동 배포.

## 부트스트랩 (VM 프로비저닝 후 1회)

```bash
# 0. (PD, 유료) GCE e2-medium @ asia-northeast3, 영구디스크, gcloud 인증.
#    Secret Manager에 .env 본문을 'rianileo-env' 시크릿으로 생성.
# 1. 부트스트랩
sudo DEPLOY_REPO=https://github.com/ahnbingbing/rainileo_agent bash deploy/bootstrap.sh
# 2. SHADOW로 검증 (라이브 아님): /etc/rianileo/env 에서
#      YOUTUBE_AUTO_UPLOAD=0  +  SLACK_* = dev 워크스페이스
systemctl enable --now rianileo-bot.service rianileo-deploy.timer
journalctl -u rianileo-bot -f          # 봇 연결·BrokenPipe 소멸 확인(P2a)
```

## 컷오버 (1주 패리티 후, 원자적)

```bash
# VM: 라이브로 승격
sudoedit /etc/rianileo/env       # YOUTUBE_AUTO_UPLOAD=1, SLACK_* = 운영 워크스페이스
systemctl restart rianileo-bot.service
# Mac: 같은 순간 18개 launchd 잡 내림 (icloud-sync만 남김 = 새벽 델타)
for j in slack slack-sync board-escalations ytcache launch producer writer \
         bandit-collect bandit-report api-cost daily-metrics bgm-claim-sync \
         gmp-morning gmp-evening giri-weekly petlabel-backlog remind-photos; do
  launchctl unload ~/Library/LaunchAgents/com.rianileo.$j.plist 2>/dev/null
done
# 최종 DB 동기화로 VM을 권위본으로(스냅샷 → VM).
```

## 검증/주의 (코드 들어가기 전 확인)

- **C-writer.** ✅ 해결 — `writer` plist는 KeepAlive=false + Cal 18:00 = **cron 잡**(데몬 아님).
  `crontab.vm`에 18:00 라인 추가됨.
- **C-ingest.** 새벽 델타 등록(`ingest_register`, 작업2)은 아직 미구현 → Mac 매니페스트를
  소비하는 VM 타이머는 작업2 완료 후 추가.
- **C-deps.** `requirements.txt`에 osxphotos가 있으나 VM에선 import 안 함(함수 내부 lazy,
  [sync_split_design.md](../notes/sync_split_design.md) C1). pip 설치는 되지만 호출 금지.
- **C-perms.** 배포자는 `rianileo` 유저로 돌고 `sudo systemctl restart rianileo-bot`만
  NOPASSWD 허용(bootstrap 7단계).
- **C-fonts.** 캡션 두부(□) 방지 = fontfile 풀패스(고차 #1). VM은 Nanum=fonts-nanum,
  Pretendard=릴리스 zip. 첫 렌더에서 캡션 렌더 검증.
