# Session handoff — 2026-08-21

**스파인:** 프레임이 ground truth. 리뷰 받으면 **재캡션 vs 재선택** 판정(캡션이 프레임/스토리와 어긋남=재캡션,
footage가 틀림[재탕·동일클립·주체]=재선택). 이번 세션 큰 교훈 — **검수기의 환각-반려는 프레임 근거로 override하라**:
Giri가 인접 에피소드의 서사를 이 클립의 ground truth로 confabulate해 clean한 컷을 벌할 수 있다(B23). 그리고
**기아 대비 완화 밸브(relax valve)는 '가장 눈에 띄는 중복'(달력상 이웃 편)에 대해선 하드 바닥을 남겨야** 한다.
이번 4건 전부 RF/메타 = **$0**(유료 API 없음).

## VM authoritative · push=deploy
- `git push origin main` → 배포 타이머(2분 폴) pull→smoke→봇 재기동. **VM HEAD = `5fd6e2b`**(코드 `52548df`,
  회고 `5fd6e2b`). 배포 후 VM에서 canon·이웃dedup·프레임 3종 검증 완료.
- SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`.
- 리포 = `/home/rianileo/rianileo-agent`. 수동 잡 = `sudo -u rianileo bash deploy/run_job.sh scripts/_xxx.py`
  (scp → /tmp → `sudo -u rianileo cp` → run_job.sh; PYTHONPATH·env 셋업됨).

## SHIPPED — PD 8/22–8/23 리뷰 4건 (라이브 비공개 예약 교체, 프레임 검증)
- **8/22 21:00 RF** `XaDQ8GiEd3k`→**`_nOhrSC_Jlg`** — 번인 캡션이 개를 `'랑이 언니'`(랴니 오기 + 틀린
  남매어)로 불렀다. 프레임 실측 = 레오가 코 인사만 살짝 하고는 관심 뚝, **돌아앉아 자기 그루밍(귀 벅벅·세수)→토라짐**.
  "코 인사 대성공/누나바라기" 오버클레임 → "허무한 인사, 누나는 관심도 없네" 도도-마이웨이로 정직화. recaption_finish.
- **8/23 08:00 RF** `8nh-ZccPvN8`→**`X2S7zv9QxhU`** — PD "8/22 1230RF랑 동일한 영상". 실측 = **동일 소스클립**
  (`med_2026_08_18_230111` 로봇청소기)을 두 슬롯이 공유. → 신선 재선택: 레오 첫 풀숲 산책(`med_2026_08_17_031015`,
  목줄 데뷔 콜백). **★Giri 5/10 confabulated-reject**(8/20 AV '레오 가출' 서사를 이 클립 ground truth로 발명,
  "레오가 과거 장소 알아보고 울었다·랴니 동행" 누락이라 벌함) → 프레임+캡션 clean 확인 후 frame-evidence override.
- **8/23 18:00 RF** `Oq2wSAjX6hA`→**`356YdN7_mNo`** — PD "아직도 뒹굴뒹굴… 완전 죽은 듯 자는 레오, 이거 고양이
  맞나요? 실화? 이래야지". 번인 = `'뒹굴뒹굴 시전!'`(8s) + `'슬금슬금 몸 뒤집'`(27.4s, 실제 뒤집힘 ~37s보다 이름).
  → dead-asleep 프레이밍("이거 고양이 맞나요? 실화?")·능동 롤 언어 제거·뒤집 비트 27s→**37s 시간정렬**. recaption_finish.
- **8/23 21:00 AV** `g8uzzd60rGg` (재렌더 X) — PD "1230AV랑 내용 같네(둘 다 랴니 수영)… 이게 더 잘 만들었네.
  둘 다 유지하되 향후엔 다음편 느낌 나도록". → **시퀄 리타이틀**(스니펫만, publishAt 보존): "🌊 오후 2탄: 수영왕
  랴니의 하천 마스터클래스 — 아침보다 깊은 물…". `youtube.videos().update(part=snippet)` + 카드 동기화.

옛 3편(XaDQ8GiEd3k·Oq2wSAjX6hA·8nh) veto 확인, 라이브 스케줄 4/4 신규 반영·고아 0.

## durable 코드 — `52548df` (review-learning+prompt-authoring+pipeline-change-impact+merge-retrospective 스킬)
1. **canon 이름 KO 드리프트** — `_CANON_NAME_FIX`에 `(?<![가-힣])랑이→랴니`(한글 lookbehind로 `사랑이` 안 먹음),
   `랴니 언니→랴니 누나`(레오=막내라 호칭은 누나/랴니엄마). 번인+업로드 chokepoint 양 레인, 멱등. 유닛 7/7.
2. **RF 하드 이웃-dedup** — `_scheduled_window_rf_assets(con, target, ±3일)`: 소프트 쿨다운이 얇은 신선 풀에서
   **통째 완화**되어 이웃 예약편의 exact 클립을 되돌리던 근본(08:00≡전날 12:30). 완화-불가 하드 백스톱으로 선택
   pool에서 제외. `_propose_realfootage_singlepass`에 배선 + `_excl`에 fold(photo/archive 커버). **VM 검증**:
   `_scheduled_window_rf_assets(8/23)` = 14개, 8/18 dup 포함 → 차단 확인.
3. **`_RF_ACTION_SYS` (RF 캡션 생성기)** — dominant-state framing(대부분 정지면 그 정지가 스토리, 드문 한 번의
   뒤척임을 테마로 승격 금지=단발·LATE 비트) + **모션 비트 시간정렬**([start,end]가 그 동작이 화면에 있는 초를 덮어야).
4. **AV 재탕검증 directive** — 소재가 그날 footage 사정상 불가피하게 겹치면 억지 재제안 대신 뒷 슬롯을 앞 편의
   **'2탄/다음 편'**으로 이어라(시간·난이도 진전). Giri 교차일 dup 캡의 기존 '~1탄/2탄' 예외(reviewer.py:276)와 이미 정합.

회고 반영(`5fd6e2b`): §4.4 **C17(+KO 랑이)·C_neighbordup·C_stillframe**, §4.3 **B23**(Giri confabulated-backstory),
§4.6 **E12**(소재 겹침→시리즈).

## gotcha
- **recaption_finish**은 `wd/animated/<tag>.mp4`(uncaptioned native)에서 재-번인 → double-burn 없음. RF 단일컷
  태그 = `cut1_intro`. `_tempo_factors` 없으면 assemble가 1.3x로 ~23% 짧아짐 → 캡션 JSON에 `{"cut1_intro":1.0}` 명시.
- SSH `--command "... | tail -N"`는 **명령 종료 전까지 tail이 버퍼**해 interim 출력 안 보임 → run_in_background로
  띄우고 완료 알림 대기(중간 폴 무의미). 긴 RF 렌더+Giri+업로드 = 2CPU에서 10–20분 가능(멈춘 게 아님, workdir/PID로 확인).
- `_render_realfootage_direct`의 concept 캡션은 **seed일 뿐** — render_card의 VLM 캡션 에이전트가 프레임서 재작성
  (내 하드작성 캡션 덮음). 내 캡션 고정하려면 recaption_finish(VLM 없음). 이번엔 VLM 캡션이 마침 좋아 그대로 채택.
- scp는 `/tmp`로만(직접 `/home/rianileo` 권한실패) → `sudo -u rianileo cp`.

## ★NEXT
- **다음 03:00 배치 첫 실전 스팟체크**: RF 이웃-중복 실제 감소(같은 클립 이웃 슬롯 재등장 0)·dead-still/timing
  캡션·과반려 없는지.
- **★미fix = Giri confabulated-backstory reject(B23) giri-update** — Giri가 인접 에피소드 서사를 이 편 ground
  truth로 발명해 clean 클립을 벌함. RF self-heal이 수정필요에 재롤 → 좋은 클립이 배치서 반복 튕겨 슬롯 빔 위험.
  "문서화된 character_facts/컨셉 밖의 서사를 발명해 벌하지 말라" 규칙 필요(회귀 동반, giri-update 스킬).
- (이월) `'라니엄마'` 이름 교정 미완(8/19 핸드오프) — canon 랑이 fix가 부분 커버.
