# Session handoff — 2026-07-09 (7/9 AV 전멸 근본수정+복구 → 7/10 RF 캡션-안바뀜 근본수정)

스파인: **"증상 위에 밴드에이드가 아니라 원인." 두 사건 모두 근본원인을 파고 결정론으로 막았다.**
① 7/9 AV 4편 전멸(giri clip-reuse 오발화) → 근본수정+양슬롯 복구. ② 7/10 RF1800 캡션이 안 바뀜
(정적 낮잠 단일캡션 + Giri 프롬프트룰 러버스탬프) → 검수기 결정론화 + 생성기 cadence. 모든 durable
변경 push+VM 배포 확인(VM HEAD = **f863d78**).

## VM authoritative. push=deploy.
`git push origin main` → deploy 타이머(2분 폴)가 pull→smoke→봇 재기동. 검증은 **VM HEAD**로. VM 렌더 실행:
`PATH=/home/rianileo/.local/bin`(ffmpeg text_align) + `sudo -u rianileo bash -c 'set -a; source /etc/rianileo/env; set +a; …'`
+ `PYTHONPATH=/home/rianileo/rianileo-agent`. 새벽 배치 = crontab line `0 3 * * * launch_selfheal`(03:00 KST).
로그는 `data/logs/cron.*.log`(봇 로그툴이 옛 파일명 batch_problems/launch_out을 봐서 "로그 없다" 오진 — 미수정).

## 1. Durable ships (전부 배포)

### 7/9 AV 전멸 근본수정 (사건 ①)
- **giri clip-reuse → real_footage 전용**(d1772b6): `_clip_reuse_gate`(+launch propose seed)가 양 레인에 걸려
  AV asset_id(=포즈/생성 레퍼런스, 재사용 정상)를 "화면 클립 재사용"으로 오판 → AV 4편 giri_fail 전멸. RF 전용
  스코프. 회고 **B9**. producer 쿨다운들은 원래 RF-only라 무관.
- **board 재시작 resume**(54d0705→0ed0308): deploy `systemctl restart`가 처리 중 board 답변(데몬 스레드)을 죽여
  ack만 남고 답 유실 → 반복되던 "board 무응답". 시작 시 최신 미응답 board 메시지 1회 자동 재처리(thread-aware,
  ack는 답으로 안 침, handled/resumed를 메시지 ts로 키잉). 회고 **D23**.
- **prune in-flight 보호**(5e988d3): `_prune_tmp_workdirs`가 최신 6개만 유지 → 예약된 7/9 AV의 $0-salvage
  workdir(캡션 재작업 재료)를 삭제 → 유료 재렌더 강요. 이제 공개前/최근3일 공개 에피소드 workdir 보호
  (`CAMERAMAN_SALVAGE_DAYS`). ★최종본(`data/output/episodes/`)은 원래부터 prune 무관. 회고 **D22**.

### 7/10 RF 캡션-안바뀜 근본수정 (사건 ②, PD가 "원인부터"라고 지적)
- 증상: 7/10 RF1800 '이불 요정 낮잠' = 20.9s 단일 클립인데 캡션 **한 줄로 20.9s 유지**(프레임 확인). payload엔
  10-scene 있었으나 **실제 번인은 1-scene**(payload≠번인).
- root(2겹): ①생성기 `_rf_action_grounded_captions`가 "동작 바뀔 때 beat 분할"이라 정적 낮잠엔 beat 1개 ②Giri
  '캡션 안 바뀜' 체크가 **프롬프트 룰**(a6fc4b9, 7/8 추가)이라 낮잠을 "매력적"이라 칭찬·통과 = **룰 넣은 지
  이틀 만에 러버스탬프**(회고 B8의 재판 → **B10**).
- fix(c1dbabf, 양쪽): 검수기 `_caption_hold_gate` 결정론(**workdir captions.json=실제 번인분** 읽어 한 scene이
  `RF_CAPTION_MAX_HOLD_S` 이상이면 cap≤6). 생성기 `_RF_ACTION_SYS`에 최소 cadence.
- **PD 튜닝**(f863d78): "10초는 너무 길다" → 생성기 타깃 **~4-5s마다 beat·캡션 ≤~6s**(~⌈dur/5⌉ 개), backstop
  **10→8s**. 회귀: 20.9s 발화 / ~5s×4 통과 / 봇 재렌더 ~7s 통과(안 튕김).

## 2. 복구·상태 (라이브 확인)
- **7/9 AV 두 슬롯 공개됨**(ytcheck0709 자동확인, board 리포트): 08:00 `DzYCCh7ecQA`(감정색) + 18:00
  `xv-TsQkCTu0`(생선 먹튀). 044926은 무비용 예약, 031859는 재렌더(PD승인)+수동 리캡션.
- **7/10 RF1800**: 봇의 캡션보존 재렌더 완료 → 새 영상 **`3BFpOGZ7tmw`**(3-beat) 18:00 재예약(기존 CpjCryEQgb8
  대체). 이 영상은 c1dbabf 前 렌더라 ~7s beat(8s 게이트 통과). 캡션 바뀜.
- 일회성 타이머 `ytcheck0709`는 발사·보고 완료(auto-disable).

## 3. ★ NEXT (다음 세션)
1. **7/10 03:00 배치 스팟체크(=7/11 콘텐츠, 새 캡션로직 첫 프로덕션)**: RF 캡션이 ~4-5s마다 바뀌는지, 정적
   클립이 단일캡션으로 안 새는지(_caption_hold_gate 발화 시 self-heal이 재제안하는지 vs 슬롯 비는지).
2. **7/10 공개 확인**: 08:00/18:00 AV + RF1800(3BFpOGZ7tmw) 등 슬롯이 예정대로 public 되는지.
3. **board 봇 잔여 결함 2건**(둘 다 이번 세션서 노출, 미수정): ①봇이 **원인 진단 없이 재렌더부터** 건다(PD가
   "원인부터"라고 지적) — "증상수정 전 원인" 규율이 봇엔 아직 없음. ②봇 **로그툴이 옛 파일명**(batch_problems/
   launch_out/launch_err/slack_err)을 봐서 "로그 없다" 오진 → `data/logs/cron.*.log` 읽게 고쳐야.
4. 생성기 cadence가 정적 클립에 **억지 micro-beat 패딩**을 만들지 PD 스팟체크(낮잠 3-4beat가 자연스러운지 vs
   같은 상태 반복인지). 너무 패딩이면 "정적 클립은 애초에 짧게 트림/선택 안 함" 쪽으로.
