# Session handoff — 2026-07-22

**스파인:** 이번 세션의 반복 주제는 **"증상의 상관 패턴은 오진을 유도한다 — ground truth(로그·DB·라이브)를
먼저 확인하라."** remote 로그아웃도, 07-23 이중예약도, 윙크 이질감도, 폴라로이드 결함도 — 겉보기 원인과 진짜
근본이 달랐다. 그리고 유료 렌더 없이 **오프라인/싱글컷으로 먼저 확정하고 최소로 손댔다.**

## VM authoritative. push=deploy.
- `git push origin main` → 배포 타이머(2분 폴)가 pull→smoke→봇 재기동. **VM HEAD = `dcd78f4`**(이 세션 끝, 로컬·origin·VM 동기화).
- VM: `rianileo-veo` / `asia-northeast3-a` / `rianileo-brain`. SSH = `gcloud compute ssh rianileo-brain --zone=asia-northeast3-a --project=rianileo-veo --tunnel-through-iap`. **IAP 간헐 실패(첫 시도 자주 실패 → 재시도로 붙음)**.
- ★**중첩 `sudo bash -c` 안 인라인 python/heredoc 이스케이프가 계속 깨진다** — 작은 `.py`/`.sh`를 `gcloud compute scp`로 올려서 실행(핸드오프 반복 교훈, 이번에도 여러 번 당함).
- **YouTube 상태는 라이브 API로 확인**(DB stale 가능).

## 배치 = D+2 (LAUNCH_LEAD_DAYS=2, 확인 완료)
- 라이브 crontab line 21 `LAUNCH_LEAD_DAYS=2` 살아있고 run_job env 체인 거쳐 `RESOLVED_LEAD=2`(env파일 오버라이드 없음). 증거: **07-22 03:00 배치가 07-24를 만듦**. 다음 배치 07-23 03:00 → **07-25 생성**, 이후 계속 D+2.
- ★**crontab은 배포로 자동 재설치 안 됨**(설치는 `deploy/bootstrap.sh` 한 곳뿐, `pull_deploy.sh`는 코드만 pull). `deploy/crontab.vm`를 또 바꾸면 VM에서 `crontab -u rianileo deploy/crontab.vm` **수동 재설치** 필요. (durable 훅으로 만들자던 건 미실행 — 아래 미완.)

## SHIPPED (durable, 배포·검증)
1. **예약 고아 근본 + reconciler (`bd12680`, [[schedule_orphan_reconciler]])** — 07-23 이중예약/07-24 얇음의 진단.
   ★**첫 진단 오답**: YouTube 예약 패턴만 보고 "크론이 LEAD_DAYS 못 받아 07-23 만듦"이라 결론냈으나, VM 크론 로그가
   ground truth였다 — LEAD_DAYS=2 정상(07-24 타깃), 07-24 얇음은 **렌더 3/4 실패**(self-heal이 junk 대신 빈슬롯,
   설계대로: content_gutted·caption미스매치·SEEDANCE_MAX_CALLS), 07-23 잉여 3편은 **추적 안 되는 예약 고아**.
   근본=`_auto_upload_episode`가 카드 `youtube_video_id`를 **옛 영상 veto 없이** 덮어써 옛 예약본이 YouTube에
   고아로 남아 슬롯 이중예약(arc/veto 안 보임). Fix: ①공통 chokepoint가 새 업로드 성공 후 이전 id auto-veto
   (교체=replace-not-add) ②`agents/reconcile.py`(예약본↔카드 대조, `python -m agents.reconcile [--veto|--json]`)
   ③launch_selfheal 써머리에 고아 경보(빈슬롯 경보급). 회고 §4.5 D_orphan.
2. **AV 윙크 실사감 (`c1e589c`, [[av_wink_fresh_still_seed]])** — PD "요즘 마지막 윙크만 랴니 좀 달라". 근본=윙크만
   자기 fresh char-ref 스틸이 아니라 **누적 드리프트된 체인 프레임**에서 i2v seed(앞 컷은 다 fresh스틸→실사). 윙크는
   best-of-N char-ref 스틸을 만들어놓고 버렸음. Fix=윙크컷만 fresh스틸 재앵커(연속성 힌트 유지), kill
   `AV_WINK_FRESH_STILL=0`, RF 무영향. **싱글컷 VM 실렌더 검증**: push-in 클로즈업 내내 랴니(흰블레이즈·턱·꼬리없음)·
   레오 on-model 실사. 회고 §4.2 A19.

## 손수정 (예약 교체/정리, 손실 0)
- **07-23/07-24 콘텐츠 정리**: 고아 3편(gxH9EWsG-zs·jnTOEljtPpY·ZBjPqAu8vXo)을 07-23 이중예약에서 **07-24 빈슬롯으로 이동**
  (YouTube publishAt update, **API eventually-consistent라 update 응답값으로 검증**)+최소 카드 등록(card_type CHECK=daily/memory_lane)→리콘실러 clean. 결과 07-23·07-24 각 4/4.
- **7/23 AV0800 폴라로이드 cut2 손수정 (situational, PD 지시로 규칙화 안 함)** — cut2(랴니)만 `ref` 자유생성이
  폴라로이드 테두리를 떨궈 방에 풀프레임(cut1·cut3는 정상). **랴니 애니는 멀쩡, 프레임만 없음.** Fix=cut3와 동일한
  크림 라운드 테두리 PNG를 cut2 구간 `[6.53–11.57s]`에만 시간-게이트 오버레이→재인코딩(무료). reupload
  **`lgRU5oG4KVg`**(옛 9fsjASDaiP0 삭제, publishAt 07-23 08:00·제목 유지, 카드 갱신→리콘실러 clean). 워크디렉토리는
  프룬돼 pre-caption 소스 없음 → assembled 영상에 오버레이. cut 경계는 `scene` 검출(각 컷 ~5.03s).

## remote/login 매일 로그아웃 근본 (환경, repo 무관)
- 근본=**같은 OAuth 계정으로 claude CLI 세션 여러 개 상시**(장기 5일 세션 + 새 세션) → 1회용 refresh token 회전으로
  서로 무효화 → 매일 자리다툼 로그아웃. launchd 자동화는 **API 키(anthropic SDK)라 무관**. 조치: 5일 상주 세션 종료 +
  중복 설치(homebrew npm) 제거(네이티브 `~/.local/bin` 유지). `remoteControlAtStartup:true`는 설정돼 있으나 세션 시작
  때 인증돼 있어야 자동 연결. **재발 방지=CLI 세션 며칠씩 켜두지 말 것**(상주 필요시 그 세션만 API키로 분리).

## 예약 현황 (라이브 확인)
- **07-23**: 08:00 폴라로이드(수정본 lgRU5oG4KVg) · 12:30 겨울농구 MKi5jmKeW7I · 18:00 에어컨 r02-GgxIG_0 · 21:00 수영장 VSfZqNLrg8k
- **07-24**: 08:00 gxH9EWsG-zs · 12:30 jnTOEljtPpY · 18:00 s7Kgw1WdEA8 · 21:00 ZBjPqAu8vXo (이동 3편+크론 성공 1편)

## ★NEXT / 미완
1. **07-23 03:00 크론 배치(=07-25 생성) 스팟체크** — LEAD_DAYS=2 D+2 첫 정상 사이클 + **윙크 fix 첫 실전**(모든 윙크가
   fresh스틸서 렌더되는지, AV_WINK_FRESH_STILL) + reconciler 써머리 경보 동작.
2. **07-23 08:00 폴라로이드 수정본(lgRU5oG4KVg) 공개 전 PD 최종확인** — cut2 테두리 일관성(원하면 링크 제공).
3. **07-24 레인 밸런스** = 1 AV + 3 RF(이동 결과, 있는 걸로 채움). 문제면 슬롯 교체.
4. **(durable 미실행) 배포가 crontab도 재설치하게** `pull_deploy.sh`에 훅 추가 — 이번 crontab drift류 원천 차단(선택).
5. **07-24 렌더 실패 3종 하드닝**(reconciler와 별개, 기존 진행 대상): content_gutted 최소 14s, caption-clip 매칭,
   SEEDANCE_MAX_CALLS ceiling — self-heal이 계속 빈슬롯 남기면 이쪽.

## 교훈
- **상관 패턴 ≠ 근본.** 로그/DB/라이브가 ground truth(예약 패턴이 crontab 오진 유도, C3·B7 재확인).
- **"예약됨 ≠ 추적됨."** 외부 리소스를 덮는 단계는 옛 것 먼저 회수(veto)+대조기(reconciler) 필수([[verify_youtube_state_via_api]] 반대방향).
- **좋은 스틸을 만들고도 다운스트림서 버릴 수 있다**(윙크). 캐릭터 충실도=ref 유무를 넘어 "매 컷이 그 ref스틸서 seed되냐".
- **유료 렌더 전 오프라인/싱글컷 확정.** 폴라로이드는 $0 오버레이, 윙크는 싱글컷 1회로 검증.
