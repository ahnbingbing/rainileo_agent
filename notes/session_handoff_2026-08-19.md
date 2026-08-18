# Session handoff — 2026-08-19

**스파인:** 프레임이 ground truth. 리뷰 받으면 **재캡션 vs 재렌더 vs 재선택** 판정(캡션이 프레임/스토리와
어긋남=재캡션, footage가 틀림[재탕·pre-Leo·주체안보임]=재선택, 소품/캐릭터/윙크 드리프트=재렌더). 이번 세션
큰 교훈 — **검수기는 "생성기가 못 잡는 것"뿐 아니라 "생성기가 이미 요구/금지하는 것"까지 enforce해야 러버스탬프를
면한다**(생성기 갱신≠체커 갱신, 둘을 lockstep으로). 그리고 감지만 하고 못 고치는 체커(fix 표현력 부족)는
flag-only=사람이 또 손수정.

## VM authoritative · push=deploy
- `git push origin main` → 배포 타이머(2분 폴) pull→smoke→봇 재기동. **VM HEAD = `283a23c`**.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
- 리포 = `/home/rianileo/rianileo-agent`(rianileo 소유). 명령 = `sudo -u rianileo bash deploy/run_job.sh …`.
  긴 렌더는 **런처 .sh 커밋→VM pull→`sudo bash scripts/_launchXXX.sh` 짧은 호출**(systemd-run --collect,
  SSH drop 안전). 좀비 SSH 루프 여럿=IAP 터널 고갈→255, 이번 세션은 단일·간격 SSH로 무사(좀비 0 확인).

## 핸드오프 체크 결과 (8/18 핸드오프 ★NEXT 검증)
- **1230 AV 완주** — avfin1230(레오 윙크) 완주·예약 `G_85gNhkihk`(8/19 12:30), 옛 Zkrwq9ziMUk veto. 8/19 4/4 예약 정상.
- **PD 일일 리뷰어 첫 실전 성공** — 8/18 09:40 cron이 8/20 배치(D+2) 검수: 3편 clean + 21:00만 플래그
  (콜라주 pre-Leo), **그마저 2-pass 미합의로 미적용(보고만)**. **과churn 0** = 설계대로 안전 동작 →
  `PD_REVIEW_APPLY=1` 유지 OK. durable(그해 스크러버·pre-Leo floor·mismatch게이트)은 8/19 배치에 이슈 0으로 간접 확인.

## SHIPPED — PD 8/20 리뷰 2건 (라이브 비공개 예약 교체, 프레임 검증)
- **8/20 21:00 RF** `oby-SQrHcDg`→**`IvDDPE-Fa88`** — PD "어린 랴니가!"+"왜 캡션이 하나로 끝이야?". 근본=원래
  컨셉은 med_2019_11_24 **어린 랴니(pre-Leo) 강가 데크 멀티캡션**이었는데 **salvage 경로가 단일 generic("랴니가
  벤치에 앉아…")으로 뭉갬**. → 어린 랴니 pre-Leo **5비트 메모리레인** 복원(보라 하네스·강가 데크·갈대·고개 돌림,
  wistful 클로저=레오 등장 복선), 프레임 검증(한글 tofu 0·비트 매칭).
- **8/20 08:00 AV** `S9kEGX51LGY`→**`wCmOwjyHs4U`** — PD "컨셉이 하나도 없다"(명당 쟁탈전). PD 실화로 교체=
  **레오 반나절 가출**(하비가 문 연 찰나 탈출→누나 랴니 냄새 나는 곳 탐험→해질녘 "레오레오" 울음→함미 발견→그래서
  요즘 목줄 생김). render_av_one 디렉티브, Writer 6막→5컷 축약, 프레임 검증=**야외 테라스/현관/한옥 노을**(로그의
  set_anchor=home_livingroom는 무시됨, 실제 렌더는 디렉티브대로 야외 — 프레임이 ground truth), 빨간 목줄 페이오프
  시각 확실, 레오 윙크·랴니 클로저 등장. 옛본 veto.

## durable 코드 — `86c1fa0` (review-learning+prompt-authoring+merge-retrospective 스킬로 merge)
**PD 일일 리뷰어 강화.** 첫 실전서 ①hook 없는 flat 제목("랴니가 벤치에 앉아…"=「펫이 ~하고 있어요」 라벨링)
②22.5s 단일 컷 flat 캡션(salvage 뭉갬)을 통과시킨 갭. **근본="생성기는 갱신됐는데 체커가 안 따라감"** — 생성기는
이미 flat 제목 금지(realfootage_concept "단순 클립 라벨링 아님")·캡션 cadence 요구(~4-5s/beat `_caption_hold_gate`)
인데 pd_reviewer는 "생성기가 못 잡는 것"(제목 거짓말·pre-Leo era)만 enforce → salvage가 생성기 규칙 우회 시 무방비.
게다가 recaption 픽서가 컷당 단일 `{ko,en}`만 표현 가능 → 밀도 감지해도 못 고침=flag-only.
- **pd_review.md**: Check1 flat-but-accurate 제목=결함(훅 필수)·Check2 캡션 density(긴 클립 단일 flat=결함,
  ~1비트/4-6s·씬≥2.5s, ~22s면 4-5비트)·recaption 스키마에 멀티씬 `scenes[]`.
- **pd_reviewer.py**: `_do_recaption`이 `scenes[]` 소비(1→N split/재타이밍), `_sanitize_scenes`로 컷 실길이
  clamp·짧은씬(<1s) 드롭·정렬. 멀티씬 컷에 단일 텍스트만 오면 여전히 skip(cram 방지). → **"per-scene recaption
  미지원" TODO 해결**(RF narrator 멀티씬 자동수정 활성).
- **Giri(reviewer.py) 미추가** — 제목/캡션밀도는 full-title+all-captions 보는 pd_reviewer 소관(Giri=2프레임·mp4만).
- 검증: `_sanitize_scenes` 단위테스트(clamp/드롭/정렬/garbage 무크래시) + 수정된 8/20 배치 dry-run서 내 21:00
  재캡션 **clean(새 density/hook 체크가 좋은 멀티비트 콘텐츠 오탐0)·회귀0·무크래시**. 회고 **B22**·메모리
  `pd_reviewer_title_hook_caption_density`.

## gotcha
- **systemd-run `--collect` 유닛은 종료 후 저널까지 GC** — 진행/결과 저널은 유닛 **active일 때** 폴해야 보임
  (끝나면 `journalctl -u <unit>` 비어짐). 완주 결과는 카드 DB의 youtube_video_id / 라이브 스케줄로 확인.
- **AV set_anchor 로그 ≠ 실제 컷 렌더** — 08:00 AV가 `set anchor: home_livingroom`으로 로깅됐지만 실제 컷은
  디렉티브대로 야외였다. 앵커 로그 반사적 불신 금지, 프레임 확인.
- 렌더 완주 폴링=**간격 둔 짧은 SSH**(로컬 sleep + is-active/journal 짧은 체크)로 터널 보호. held-SSH 40분 금지.
- 손수정 video_ids: 21:00 `IvDDPE-Fa88`, 08:00 `wCmOwjyHs4U`.

## ★NEXT
1. **PD 리뷰어 새 체크(density/hook) 첫 실전** — 다음 09:40 cron이 신선 배치를 검수할 때 새 캡션-밀도/제목-훅
   체크가 **과churn 없이** 도는지(특히 정상 멀티비트/훅 제목을 오탐 안 하는지) Slack 보고 스팟체크. dry-run은
   오탐0이었으나 라이브 첫 판 확인. 문제면 cron서 `PD_REVIEW_APPLY=1` 제거→flag-only.
2. **8/20 08:00 AV cut5 캡션** — dry-run서 리뷰어가 story-truth 뉘앙스로 flag(새 체크 아님, 기존 Check2).
   **PD "let it go"** → 손대지 말 것. 리뷰어 2-pass가 자율로 통과/수정.
3. durable 첫 실전(그해 스크러버·pre-Leo floor·mismatch게이트) 03:00 배치서 과반려/과드롭 계속 감시.
4. 미완: **"라니엄마" compound 이름교정**(라니→랴니 canon 교정기 미적용, durable 후보). ※"recaption per-scene
   스키마"는 이번 세션 86c1fa0로 해결됨.
