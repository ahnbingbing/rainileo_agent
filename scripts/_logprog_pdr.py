"""One-shot: log the PD-reviewer durable hardening to the shared board<->CLI log.
  sudo -u rianileo bash deploy/run_job.sh scripts/_logprog_pdr.py
"""
import sys
sys.path.insert(0, ".")
from agents.progress_log import log_progress

MSG = (
    "durable 86c1fa0 — PD 일일 리뷰어 강화(review-learning+prompt-authoring+merge-retrospective 스킬로 merge). "
    "첫 실전서 hook없는 flat제목+22.5s 단일캡션(salvage뭉갬) 통과한 갭. 근본='생성기는 갱신됐는데 체커가 안 따라감'"
    "(생성기는 이미 라벨링금지·캡션cadence 요구). Fix: pd_review.md Check1 flat제목=결함(훅필수)·Check2 캡션density"
    "(~1비트/4-6s·씬≥2.5s)·recaption 멀티씬 scenes[] 스키마 + pd_reviewer _do_recaption scenes[] 소비"
    "(_sanitize_scenes clamp/드롭/정렬, 1→N split 자동수정)=per-scene recaption TODO 해결. Giri미추가"
    "(제목/캡션밀도는 pd_reviewer 소관). 검증=단위테스트+8/20 dry-run 내 21:00재캡션 clean(오탐0)·회귀0. "
    "회고 B22·메모리 pd_reviewer_title_hook_caption_density. ★NEXT=다음 09:40 cron서 새 density/hook 체크 첫 실전"
    "(과churn 감시)+08:00 AV cut5 캡션 story-truth 뉘앙스는 리뷰어가 2-pass로 자율판단."
)

if __name__ == "__main__":
    log_progress("CLI", MSG)
    print("LOGGED_PDR", flush=True)
