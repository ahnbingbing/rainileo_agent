#!/bin/bash
# abc_cut5_test.sh
# -----------------
# Episode 1 cut5_closer 한 이미지에 세 가지 프롬프트 스타일 적용해서 비교.
# 트랙2 운영 시 어느 스타일이 가장 자연스러운 모션을 만드는지 결정하기 위함.
#
# 비교 설계 (변수 하나씩만 변경):
#   (a) baseline   — 현재 트랙2 단순화 프롬프트 (37단어, 3문장, 카메라 정지)
#   (b) compressed — 5/11 스타일 압축 한 줄 (17단어, 카메라 미지정 = sora-2 추론)
#   (c) dual+pushin— 양쪽 모션 명시 + 카메라 push-in 결합 (40단어, 4문장)
#
# 비용: 3 × $0.40 = ~$1.20
#
# Usage:
#   bash scripts/abc_cut5_test.sh
#
# 결과는 data/output/animated/abc_test/cut5_{a,b,c}_*.mp4 + 같은 폴더의
# .meta.json sidecar (animate_hero.py 5/13 패치로 자동 저장됨).

set -euo pipefail

cd "$(dirname "$0")/.."

IMG="data/output/decorated/cut5_closer.png"
OUT_DIR="data/output/animated/abc_test"
mkdir -p "$OUT_DIR"

if [ ! -f "$IMG" ]; then
  echo "ERROR: $IMG 없음. scripts/decorate_all_cuts.sh 먼저 돌려야 함."
  exit 1
fi

echo "==> A/B/C test on cut5_closer (3 × \$0.40 = ~\$1.20)"
echo "==> input: $IMG"
echo "==> output dir: $OUT_DIR"
echo

FAILED=()

run_one() {
  local label="$1" prompt="$2"
  local out="${OUT_DIR}/cut5_${label}.mp4"
  if [ -f "$out" ]; then
    echo "==> ${label}  (already exists, skipping — delete to re-run)"
    echo
    return 0
  fi
  echo "==> ${label}"
  echo "    prompt: ${prompt}"
  set +e
  python3 scripts/animate_hero.py \
    --image "$IMG" \
    --prompt "$prompt" \
    --seconds 4 \
    --output "$out"
  local status=$?
  set -e
  if [ $status -ne 0 ]; then
    echo "  !! ${label} failed (exit ${status}) — continuing"
    FAILED+=("$label")
  fi
  echo
}

# ─── (a) baseline ────────────────────────────────────────────────────────
# 현재 animate_all_cuts.sh 의 cut5_closer 프롬프트 그대로.
# 양쪽 동물 모두 묘사 (cat=꼬리+귀, dog=고개+눈), 카메라 정지 명시.
run_one "a_baseline" \
  "An orange tabby cat and a small black French bulldog rest side by side. The cat swishes its tail and its ears twitch. The dog tilts its head and blinks. Camera holds still."

# ─── (b) compressed (5/11 style) ────────────────────────────────────────
# 5/11 통과 영상 스타일: 13~16 단어 한 줄, 동작 동사 + 감정어, 카메라 미지정
# (sora-2 자체 추론에 맡김). 단 5/11 원본은 단일 동물만 명시했지만 여기는
# 양쪽 다 짧게 — 트랙2 cut 용도이므로 양쪽 동물 모두 frame 안에 있는 게
# 자연스러움.
run_one "b_compressed" \
  "A cat and a small dog sit together, both gently blink and tilt their heads, calm and warm."

# ─── (c) dual motion + camera push-in ───────────────────────────────────
# 양쪽 동물 동시 동작 명시 ("At the same time") + 카메라 무빙 결합.
# 5/11 영상 2에서 카메라 무빙이 명시 없이도 발생한 걸 봤으니, 명시하면
# 더 의도된 방향으로 controllable 한지 검증.
run_one "c_dual_pushin" \
  "An orange tabby cat and a small black French bulldog sit side by side. The cat slowly swishes its tail. At the same time the dog tilts its head and blinks. Camera gently pushes in toward them."

echo "=========================================================="
echo "Results:"
ls -lh "$OUT_DIR"/*.mp4 2>/dev/null | sed 's/^/  /' || echo "  (no clips produced)"
echo
echo "Sidecars (prompt + video_id + status):"
ls -lh "$OUT_DIR"/*.meta.json 2>/dev/null | sed 's/^/  /' || echo "  (none)"

if [ ${#FAILED[@]} -gt 0 ]; then
  echo
  echo "Failed labels:"
  for t in "${FAILED[@]}"; do
    echo "  - $t"
  done
  echo "Re-run the same script to retry failed ones (이미 만들어진 건 스킵)."
fi

echo
echo "다음:"
echo "  1) 세 mp4 시청 — 어느 게 가장 자연스럽고 의도된 모션을 보이는지 결정."
echo "  2) 위너 프롬프트를 notes/proven_motion_prompts.json 에 entry 추가."
echo "  3) animate_all_cuts.sh 의 cut1~5 프롬프트를 위너 스타일로 일괄 통일."
