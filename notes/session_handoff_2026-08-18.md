# Session handoff — 2026-08-18

**스파인:** 프레임이 ground truth. 리뷰 받으면 먼저 **재캡션 vs 재렌더 vs 재선택** 판정(캡션이 프레임/스토리와
어긋남=재캡션, footage가 틀림[재탕·pre-Leo·주체안보임]=재선택, 소품/캐릭터/윙크 드리프트=재렌더). 이번 세션
큰 교훈 셋 — ① **프롬프트 규칙은 advisory, 물리적으로 불가능한 것(존재 이전·금지어)은 결정론 게이트로 enforce**
(그해·pre-Leo가 프롬프트로 막았는데 재발). ② **LLM 검수기를 full-auto로 켜기 전 반드시 검증**(비결정성·멀티씬
collapse로 좋은 영상 churn). ③ **내 gcloud SSH가 자꾸 255로 죽으면 로컬 좀비 백그라운드 SSH 루프를 의심**하라
(IAP 터널 고갈 — 아래).

## VM authoritative · push=deploy
- `git push origin main` → 배포 타이머(2분 폴) pull→smoke→봇 재기동. **VM HEAD = `95699d7`**(1230 런처까지).
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
- 리포 = `/home/rianileo/rianileo-agent`(rianileo 소유). 명령 = `sudo -u rianileo bash deploy/run_job.sh …`.
  ⚠️ `run_job.sh -c "<한 줄>"`만(개행/특수문자 금지). 긴 렌더는 `systemd-run --uid=rianileo …`(SSH drop 안전).
- ⚠️★ **SSH 터널 고갈 근본(이 세션 최대 시간낭비):** 백그라운드 SSH 대기 루프(`until systemctl…; sleep`)를
  여러 개 띄우면 맥에 gcloud/ssh/iap-tunnel 프로세스가 쌓여 IAP 동시연결을 고갈 → 새 SSH가 죄다 `exit 255`.
  증상=짧은 `echo`는 되는데 launch/긴 명령은 죽음. **치료: `pkill -f 'start-iap-tunnel'; pkill -f 'gcloud.py compute ssh'`
  로 로컬 좀비 정리 → 단일·간격(≥60s) SSH만.** 긴 inline `systemd-run`이 tunnel을 넘기면 **런처 .sh를 커밋→VM
  pull→`sudo bash scripts/_launchXXX.sh` 짧은 호출**로 우회(scripts/_launch1230.sh 패턴).

## SHIPPED — PD 리뷰 대량 (8/15~8/19, 전부 라이브 비공개 예약 교체)

### 8/15 (6항목)
A 시점캡션 근본 `f1150f3`(년수 정수화 `_years_ago`/`_stamp_years_ago`, "0.6년"·"그해xx" 차단) · B 컨셉
인기도 `da19e26`(channel_manager.portfolio_signal 조회수톱+역할극결→concept_brainstorm) · C 광복절 AV
`bW9xaR78bJo`→후속 `lmGynp5dvcs`(대문 태극기 게양, [[seedance_flag_via_drawn_ref]]) · D 12:30 하네스
`QwITi-4-Owg` · E 18:00 소파=레오 `RfQmNjM8_PU` · F 21:00 cut5 surgical `rST0PIJ_iTs`.

### 8/17
RF0800 "레오 태어난곳" 아님 정정 후속 · **RF1800 랴니가 길냥이 쫓기(2017 pre-Leo)** → 과거⇄현재 메모리레인
재생산 `EnniMqkOidg`("어릴적 랴니는 고양이랑 안친했어요→지금은 레오랑 찐친", 금지어 제거·쫓기 명시).

### 8/18 배치 4슬롯 전부 재생산 (PD 대노 "돈써서 이걸")
08:00 캣휠 `pYdMaVm9_tY`(드리프트 해결·새벽폭주) · 12:30 간식 `12tPYodXgAk`(질투극 재캡션: 레오 2번거절→
랴니주려니 잽싸게, footage에 손·랴니 확인) · 18:00 수첩 `h7uGQfhecKk`(O/X 매컷 러닝개그) · 21:00 카페
`wt2u79JVMOI`(2024 pre-Leo재탕→현재 카페, 제목도 카페로 교정).

### 8/19 (4항목)
0800 RF `OCOvCpc7Chw`(레오 태어난곳/기억나니레오야/랴니엄마랑함께, 낙엽·출근길 제거, cut2 같은산책길로
재선택) · 1800 RF `WHTaK9Lb9Zw`(그해가을🍂→몇년전개울💦) · 2100 AV `UJYN-7J8Hs8`(마지막에 "시원해지면
공원산책 가고싶어요" 소망캡션) · **1230 AV `avfin1230` 렌더 중**(비와서 산책취소→시무룩 랴니, 마킹고정, PD
확정대로 **레오가 윙크**[랴니 강제윙크=개가 부자연스러워 Seedance hang/timeout, PD가 레오로 결정]).

## durable 코드 (전부 배포·검증)
1. **VLM pre-Leo 중립화** `f693449` — Leo 2025-09-25생, 그 이전 footage에 VLM이 주황 길냥이를 "leo"로
   환각(프롬프트 `_temporal_grounding`은 무시됨). `tag_assets_vlm.update_asset_tags`에 결정론 `_pre_leo_neutralize`
   (captured<exists_from면 subjects서 leo제거·레오→고양이·focus조정) + `backfill_pre_leo`(`--backfill-pre-leo`)로
   **194개 백필**. [[vlm_pre_leo_neutralize_and_0818_roots]].
2. **러닝개그 첫컷부터** `901e40a`(writer_story.md) — 명명된 채점/도전 포맷은 모티프를 cut1부터 매 비트에, 끝은
   누적 페이오프(수첩 O/X가 마지막컷만이던 근본).
3. **제목-내용 불일치 근본** `9795b7e` — `actual_captions_for_video`가 recap 파일명에 렌더스탬프 없어 []→stale
   theme제목(카페를 '집 아지트'로). fix: recaption_finish가 workdir captions.json 덮어씀 + `actual_captions_for_card`
   폴백 + grounded 제목을 cards.theme 역기록.
4. **Giri 결정론 mismatch게이트** `9795b7e` — `caption_vs_clip_mismatches ≥2→cap5`·프레임근거 규칙이 프롬프트전용
   →LLM 자가적용(환각-반려). `_caption_mismatch_gate`: RF 펫정체/부재 환각 스크럽+실제불일치만 코드캡+환각유일근거
   false-reject 구제(GIRI_MISMATCH_GATE=0 revert).
5. **pre-Leo RF 선택 floor** `91c7e73`(photo_selector `_rf_era_floor`) — 현재RF는 captured≥2025-09-25, 메모리레인
   (어릴적/N년전/아기) 면제. AV_ERA_FLOOR 미러(21:00 재탕 근본, 백필은 태그만 지우지 선택은 못막음).
6. **결정론 "그해" 스크러버** `12be0f6`(canon.correct_canon_age_text에 fold) — 메모리레인 "그해 가을"→"몇 년 전
   가을", 프롬프트 ban이 계속 새어 8/19 재발 → burn/upload chokepoint서 강제. idempotent.

## ★ 신규 시스템: PD 일일 리뷰어 (agents/pd_reviewer.py, cron 09:40 KST, APPLY=1)
PD "매일 내가 손 검수하듯 검수하는 에이전트 필요" → 배치 렌더+Giri 후 **예약된 4편을 조밀 프레임 + 라이브제목 +
번인캡션 + 소스날짜/재탕을 DB·라이브 대조**로 재검수, Giri(sparse 2frame 결함게이트)가 못보는 제목↔내용/스토리진실/
컷간 소품·캐릭터드리프트/카페-집/pre-Leo/재탕/러닝개그/내용없음을 잡고 **자동수정**(retitle/recaption/reselect/rerender)
→재예약→veto→Slack보고. 프롬프트 `agents/prompts/pd_review.md`. Giri MD도 업그레이드(shorts_review_agent_giri.md §I:
스토리진실·일관성·장소·러닝개그·밀도+프레임근거).
- **검증에서 3버그 잡아 고침(그래서 검증 먼저가 옳았다):** ①reuse 오탐(AV포즈ref·같은날)→RF+다른날만 ②**LLM
  비결정성**(dry-run 통과한 캣휠을 apply-run이 스퍼리어스 재캡션→churn)→**2-pass 합의 게이트**(2번째 패스가 같은
  (class,action) 재확인해야 적용, 단일패스=보고만) ③**recaption 멀티씬 collapse**(narrator 5씬을 한덩어리로 뭉갬)
  →멀티씬 컷 스킵(단일씬만 자동). 커밋 시퀀스 9baa4c3(flag-only기본+2pass)→…→APPLY=1(gate검증 후 PD승인).
- **미완/주의:** ①recaption per-scene 스키마 없어 RF narrator 멀티씬 자동수정 불가(스킵만) ②cron이 D+2 신선배치
  검수(hand-work 없음)라 full-auto 적절하나 첫 실전 스팟체크 필요 ③2-pass라 apply run이 느림(~10분).

## gotcha 모음
- 수동 RF 재렌더 `_render_realfootage_direct`가 Giri 재제안으로 다른컨셉 물어올 수 있음(8/19 0800: 내 태어난곳
  concept→Giri가 "레오 울음" ground-truth로 재구성, 결과는 오히려 좋았음). recaption FAIL시 렌더 output 폴백.
- veto=set private+publishAt clear(복구=videos().update status로 publishAt 재설정+옛 veto). 8/18 churn된 08:00/
  12:30을 pYdMaVm9_tY/12tPYodXgAk로 이렇게 복구함.
- AV 랴니 강제 윙크=Seedance가 개 부자연스러워 캐릭터 재생성 루프→API hang. 레오 윙크는 안정.
- "라니엄마"류 compound에 canon 이름교정기(라니→랴니) 안 걸림(0800 미세오타, durable 후보).

## ★NEXT
1. **1230 AV 완주 확인** — avfin1230(레오윙크) 완료시 스크립트가 자동 예약+옛 Zkrwq9ziMUk veto. 안 되면(timeout)
   런처 재호출 or recaption 폴백. 프레임 검증(비 캡션·랴니 마킹·레오 윙크).
2. **PD 일일 리뷰어 첫 실전 스팟체크** — 다음 09:40 cron이 신선 배치를 2-pass·멀티씬가드로 자동수정하는지, 과churn
   없는지 Slack보고 확인. 문제면 cron서 `PD_REVIEW_APPLY=1` 제거→flag-only.
3. durable 첫 실전(그해 스크러버·pre-Leo floor·mismatch게이트) 03:00 배치서 과반려/과드롭 감시.
4. 미완: recaption per-scene 스키마(RF narrator 자동수정), "라니엄마" compound 이름교정.
