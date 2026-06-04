"""
scripts/decorate_cut.py
-----------------------
원본 사진 한 장 (또는 동영상의 대표 프레임 한 장) 을 OpenAI gpt-image-1
"image edit" 엔드포인트로 보내서 — 펫은 그대로 두고, 그 주변에 통일된
파스텔 kawaii 스티커 + 코너 글로우 + 배경 트윙클을 그려서 — 9:16
세로 PNG 한 장을 받아오는 스크립트.

흐름
----
원본 (jpeg/png/heic/mov)
  ↓ HEIC 디코드 / EXIF 보정 / 비디오면 중간 프레임 추출
  ↓ OpenAI images.edit (gpt-image-1, size=1024x1536, transparent=false)
  ↓ scale + center-crop → 1080x1920
출력: data/output/decorated/<asset-stem>__<timestamp>.png

이게 끝나면 그 PNG 를 animate_hero.py 한테 --image 로 넘겨서 Sora 2 로
짧은 모션 클립을 만든다 (해당 모듈은 이미 존재).

사용법
------
    # asset_id 로 (data/agent.db 자동 해석)
    python3 scripts/decorate_cut.py \\
        --asset-id med_2026_05_06_203421_icloud_331110de \\
        --subject ryani

    # 임의 경로
    python3 scripts/decorate_cut.py \\
        --image ~/Pictures/cut1.jpg \\
        --subject leo \\
        --quality high

    # 동영상 컷의 t=2.0s 프레임만 데코
    python3 scripts/decorate_cut.py \\
        --asset-id med_2025_12_14_152903_icloud_ad7fb05a \\
        --subject together --frame-time 2.0

    # 프롬프트 미리보기 (API 호출 X, 비용 0)
    python3 scripts/decorate_cut.py --asset-id ... --subject ryani --dry-run

가격 (May 2026 OpenAI gpt-image-1, 1024x1536, 이미지 인풋 포함)
    --quality low     ~$0.02 / cut
    --quality medium  ~$0.04 / cut   (default — 시작용 권장)
    --quality high    ~$0.17 / cut   (최종 5컷에 적용)
"""
from __future__ import annotations

import argparse
import base64
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
OUT_DIR = ROOT / "data" / "output" / "decorated"
TMP_DIR = ROOT / "data" / "tmp" / "decorate_input"

# gpt-image-1 supported sizes that approximate portrait 9:16.
# We use 1024x1536 (≈ 2:3, the tallest portrait it offers), then upscale
# the long axis and center-crop to 1080x1920.
GEN_SIZE = "1024x1536"
TARGET_W, TARGET_H = 1080, 1920

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
HEIC_EXTS = {".heic", ".heif"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}


# ─────────────────────────────────────────────────────────────────────
# Prompt — Ligi 본인이 작성한 한글 프롬프트 그대로 사용.
# 핵심: AI 가 "사진 자체를 일러스트로 변환" 하지 않도록
#   "원본 배경/얼굴은 최대한 유지" 와
#   "스티커는 사진 위에 얹는 그래픽 오버레이" 를 강하게 명시.
# ─────────────────────────────────────────────────────────────────────
BASE_PROMPT_KR = (
    "이 사진을 랴니&레오 유튜브 쇼츠용 귀여운 장면으로 꾸며줘.\n"
    "\n"
    "[중요] 원본 사진 픽셀은 그대로 유지하고, 그래픽 오버레이는 "
    "별도 레이어로만 얹어줘. 펫의 털, 표정, 배경 텍스처, 조명은 "
    "원본 그대로 보여야 해. 사진을 일러스트로 변환하지 마.\n"
    "\n"
    "스타일 방향 — '웹툰/만화 reaction mark (감정·액션 효과)':\n"
    "이건 '예쁜 파스텔 스티커팩' 이 아니야. 한국 웹툰 (Naver Webtoon, "
    "카카오웹툰) 이나 일본 만화에서 캐릭터 감정/액션을 강조할 때 "
    "쓰는 그래픽 effect mark 야. 깔끔한 vector 아이콘 같은 느낌:\n"
    "- 가는 깔끔한 선 (얇고 자신감 있는 라인)\n"
    "- flat color fill (수채화 X, 3D 글로시 X, 그라데이션 최소)\n"
    "- iconographic 하고 graphic 한 형태 (장식 X)\n"
    "- 만화처럼 캐릭터의 감정/액션을 '말해주는' 역할\n"
    "\n"
    "써먹을 reaction mark 어휘 (웹툰/만화 vocab):\n"
    "- ✨ sparkle burst (반짝반짝) — 흰색 또는 옅은 노란색, 4점/6점 별\n"
    "- 두근두근 작은 하트 (♡) — flat 핑크 또는 빨강, 작게\n"
    "- 볼터치 (블러시) — 양 볼에 옅은 핑크 oval 두 개 또는 사선 빗금\n"
    "- halo (천사 후광) — 머리 위 얇은 노란/흰 링, gloss X\n"
    "- 집중선/속도선 (focus lines) — 캐릭터 주변 방사형 가는 선들\n"
    "- 작은 별 (☆, ✦) — 흰색/옅은 노란색, 산뜻하게\n"
    "- 음표 (♪♫) — 즐거운 분위기일 때만\n"
    "- 빈 말풍선 외곽 (글자 X, 모양만) — 정말 필요할 때만\n"
    "- 점/땀방울 — 부끄러움이나 당황한 표정에서만\n"
    "\n"
    "사용 원칙 — '강조형 (emphasis-based)':\n"
    "- 컷 전체에서 reaction mark 총 6~9개 사이. 그 이상은 산만함.\n"
    "- 이 컷의 가장 핵심 모먼트 1~2개를 짚어주는 게 목적.\n"
    "- 만화 컷처럼 — 캐릭터가 무엇을 느끼고/하고 있는지가 effect 로 "
    "읽혀야 해. 그냥 데코가 아니라 정보 전달.\n"
    "\n"
    "‼️ 배치 규칙 (얼굴 가림 금지):\n"
    "reaction mark 는 절대로 펫의 얼굴, 눈, 코, 입, 몸 위에 겹쳐 "
    "올라가면 안 됨. 항상 펫 주변의 빈 공간 — 머리 위, 옆쪽, "
    "배경 — 에만 배치. 두 펫이 가까이 붙어 있어도 그 사이 공간에 "
    "스티커를 끼워 넣어 얼굴을 가리지 마. 인터랙션을 표현할 때는 "
    "두 펫 위쪽 또는 옆쪽 빈 공간에 마크를 두고, 시선이 그쪽으로 "
    "흐르게 해.\n"
    "\n"
    "색상 팔레트 (반드시 다양화 — 핑크 단색 도배 금지):\n"
    "- 흰색 50% — sparkle, 별, halo, focus line 베이스\n"
    "- 옅은 노란색 20% — halo, 작은 별\n"
    "- 옅은 핑크 또는 핑크-레드 20% — 볼터치, 작은 하트만\n"
    "- 기타 액센트 10% — 라벤더/민트/peach 중에서 한두 개\n"
    "\n"
    "감성 가이드: 깔끔하고 모던한 웹툰 effect — 90년대 클립아트 ❌, "
    "3D 글로시 이모지 ❌, 두꺼운 흰 테두리 ❌, 수채화 watercolor ❌. "
    "vector graphic 처럼 깔끔하게.\n"
    "\n"
    "글자/캡션/워터마크/한글/영문 일체 금지. 캡션은 나중에 따로 합성."
)

# 컷마다 펫 위치/표정/관계가 다르니까 짧은 컨텍스트만 덧붙임.
# (이 fragment 도 한글로. AI 가 펫 위치를 정확히 알면 인터랙션
# 배치가 훨씬 정확해짐.)
SUBJECT_RECIPES: dict[str, str] = {
    "ryani": (
        "이 사진의 주인공은 랴니 — 검은 프렌치불독, 박쥐 귀.\n"
        "필수 강조 (이 3개만 정확히):\n"
        "  1. 랴니 머리 바로 위 halo (옅은 노란색, 가는 라인 — "
        "두꺼운 글로시 노란 도넛 ❌)\n"
        "  2. 랴니 양 볼 핑크 볼터치 두 개 (작은 옅은 핑크 동그라미)\n"
        "  3. 시선 방향 또는 코 위쪽에 작은 흰색 sparkle 1~2개\n"
        "그 외에 배경 흰색 별 2~3개 정도만. 절대 더 추가하지 마."
    ),
    "leo": (
        "이 사진의 주인공은 레오 — 작은 오렌지 태비 새끼 고양이.\n"
        "필수 강조 (이 3개만 정확히):\n"
        "  1. 레오 머리 바로 위 halo (옅은 노란색, 가는 라인)\n"
        "  2. 레오 양 볼 핑크 볼터치 두 개\n"
        "  3. 시선 끝 또는 코 옆에 작은 흰색 sparkle 1~2개\n"
        "그 외에 배경 흰색 별 2~3개. 절대 더 추가하지 마."
    ),
    "kiss": (
        "이 컷은 랴니와 레오가 얼굴을 거의 맞대고 있는 친밀한 모먼트 — "
        "한 쪽이 다른 쪽 얼굴/코에 뽀뽀하거나 nuzzle 하는 듯한 순간.\n"
        "‼️ 두 펫의 얼굴 사이 공간에는 절대 스티커 배치 금지 — "
        "얼굴이 가려지면 안 됨. 강조는 키스 지점 '위쪽' 으로.\n"
        "이 컷의 핵심 강조 (반드시 모두 포함):\n"
        "  1. 두 펫 코가 맞닿는 지점 바로 위쪽 빈 공간에 '결정적 흰색 "
        "sparkle 한 개' — 약간 큰 흰색 ✦ 또는 반짝 모양. 코/입을 "
        "가리지 않게, 살짝 위에 떠 있게. 이게 이 컷의 주인공이야.\n"
        "  2. 랴니 양 볼에 작은 핑크 볼터치 두 개 (수줍은 느낌)\n"
        "  3. 랴니 머리 위 옅은 노란색 halo (가는 라인)\n"
        "그 외 보조 (적게 — 산만해지지 않게):\n"
        "  4. 위쪽 빈 공간에 흰색 별 2~3개 (작게)\n"
        "  5. 양 옆 빈 공간에 작은 옅은 핑크 하트 1~2개만\n"
        "❌ 발바닥/리본/말풍선 추가 금지. ❌ 핑크 단색 도배 금지. "
        "❌ 두 펫 얼굴 사이/위에 겹치는 스티커 금지."
    ),
    "together": (
        "둘 다 한 프레임 — 랴니(검은 불독)와 레오(오렌지 고양이).\n"
        "‼️ 두 펫 사이 공간에 스티커 배치 금지 — 얼굴/몸 가림 금지.\n"
        "강조 포인트:\n"
        "  1. 두 펫 머리 위쪽 중앙 (빈 배경 공간) 에 작은 흰색 "
        "sparkle 또는 작은 핑크 하트 한 개 — 연결감을 위에서 표현\n"
        "  2. 각자 양 볼에 핑크 볼터치 (선택적, 표정이 보이는 쪽만)\n"
        "  3. 둘 주변 빈 공간에 흰색 별 2~3개 (분산)\n"
        "그 외 추가 금지. 둘의 얼굴/몸은 절대 가려지면 안 돼."
    ),
    "closer": (
        "마무리 컷 — 차분하지만 마지막 임팩트.\n"
        "강조 포인트:\n"
        "  1. 펫 머리 위 옅은 노란색 halo (가는 라인)\n"
        "  2. 양 볼에 핑크 볼터치\n"
        "  3. 위쪽/주변에 흰색 별 3~4개 (작게, 분산)\n"
        "하단 1/4 영역은 캡션 자리 — 거기엔 스티커 ❌.\n"
        "전체적으로 정돈된, 거의 미니멀한 마무리 느낌."
    ),
}

# 최후의 안전장치 — 사진을 일러스트화하는 실패 모드를 차단.
HARD_GUARDS = (
    "절대 규칙: "
    "(1) 원본 사진의 픽셀을 다시 그리지 마. 펫의 털, 눈, 코, 입, 발은 "
    "원본 사진 그대로여야 해. 배경(바닥, 벽, 가구)도 원본 그대로. "
    "(2) 스티커는 사진 위에 얹는 별도 그래픽 레이어로만. 부드러운 "
    "외곽선과 약한 그림자로 사진과 자연스럽게 어우러지되 사진 자체는 "
    "변형하지 마. "
    "(3) 글자, 한글, 영문, 숫자, 로고, 워터마크 일체 금지. "
    "(4) 출력은 세로 9:16 비율. 펫과 모든 스티커가 잘리지 않게."
)


def build_prompt(subject: str, extra: str | None = None) -> str:
    recipe = SUBJECT_RECIPES.get(subject, "")
    parts = [BASE_PROMPT_KR, recipe, HARD_GUARDS]
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────────────
# Source resolution & preprocessing
# ─────────────────────────────────────────────────────────────────────
def resolve_asset_path(asset_id: str) -> Path | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute(
            "SELECT file_path FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    p = Path(row[0])
    # Rebase mac-absolute paths to the current project root.
    if p.is_absolute() and "rianileo-agent" in p.parts:
        idx = p.parts.index("rianileo-agent")
        return (ROOT / Path(*p.parts[idx + 1:])).resolve()
    if not p.is_absolute():
        return (ROOT / p).resolve()
    return p


def heic_to_jpeg(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for cmd in (
        ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)],
        ["heif-convert", "-q", "92", str(src), str(dst)],
        ["ffmpeg", "-y", "-i", str(src), "-q:v", "2", str(dst)],
    ):
        if not shutil.which(cmd[0]):
            continue
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and dst.exists():
            return True
    return False


def extract_video_frame(src: Path, dst: Path, t_sec: float | None) -> bool:
    """Pull a single representative frame from a video.
    If t_sec is None, use the midpoint of the clip."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if t_sec is None:
        # probe duration → midpoint
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True,
        )
        try:
            dur = float((r.stdout or "0").strip())
        except ValueError:
            dur = 0.0
        t_sec = max(0.0, dur / 2.0)
    r = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", f"{t_sec:.2f}", "-i", str(src),
         "-frames:v", "1", "-q:v", "2", str(dst)],
        capture_output=True,
    )
    return r.returncode == 0 and dst.exists()


def prepare_input(src: Path, frame_time: float | None) -> Path:
    """Return a JPEG path suitable for gpt-image-1 image-edit input.

    Handles HEIC decode, video frame extraction, and EXIF orientation.
    """
    from PIL import Image, ImageOps  # lazy
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        raise FileNotFoundError(f"source not found: {src}")

    ext = src.suffix.lower()
    work: Path
    if ext in VIDEO_EXTS:
        work = TMP_DIR / f"{src.stem}_frame.jpg"
        if not extract_video_frame(src, work, frame_time):
            raise RuntimeError(f"could not extract frame from video: {src}")
    elif ext in HEIC_EXTS:
        work = TMP_DIR / f"{src.stem}.jpg"
        if not work.exists() and not heic_to_jpeg(src, work):
            raise RuntimeError(
                f"no HEIC decoder available — install sips/heif-convert or "
                f"run on macOS: {src}"
            )
    elif ext in PHOTO_EXTS:
        work = src
    else:
        raise ValueError(f"unsupported source extension: {src}")

    # EXIF-normalize and ensure RGB JPEG for the API
    out = TMP_DIR / f"prep_{src.stem}.jpg"
    with Image.open(work) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        im.save(out, "JPEG", quality=92, optimize=True)
    return out


def to_target_9x16(src_png_bytes: bytes, out_path: Path) -> None:
    """Take whatever gpt-image-1 returned (1024x1536 portrait) and produce
    a 1080x1920 PNG via scale + center crop."""
    import io
    from PIL import Image
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(src_png_bytes)) as im:
        im = im.convert("RGB")
        w, h = im.size
        # Scale so the SHORT side ≥ TARGET_W and LONG side ≥ TARGET_H,
        # then center-crop to exactly (TARGET_W, TARGET_H).
        scale = max(TARGET_W / w, TARGET_H / h)
        new = im.resize((int(round(w * scale)), int(round(h * scale))),
                        Image.LANCZOS)
        nw, nh = new.size
        x0 = (nw - TARGET_W) // 2
        y0 = (nh - TARGET_H) // 2
        cropped = new.crop((x0, y0, x0 + TARGET_W, y0 + TARGET_H))
        cropped.save(out_path, "PNG", optimize=True)


# ─────────────────────────────────────────────────────────────────────
# OpenAI call
# ─────────────────────────────────────────────────────────────────────
def _require_openai():
    try:
        from openai import OpenAI  # noqa: WPS433
    except ImportError:
        print("ERROR: pip install openai", file=sys.stderr)
        sys.exit(2)
    return OpenAI


def decorate(input_jpeg: Path, prompt: str, quality: str) -> bytes:
    """Call gpt-image-1 images.edit and return raw PNG bytes."""
    OpenAI = _require_openai()
    client = OpenAI()
    with open(input_jpeg, "rb") as f:
        resp = client.images.edit(
            model="gpt-image-1",
            image=f,
            prompt=prompt,
            n=1,
            size=GEN_SIZE,
            quality=quality,
        )
    return base64.b64decode(resp.data[0].b64_json)


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src_group = ap.add_mutually_exclusive_group(required=True)
    src_group.add_argument("--asset-id", help="data/agent.db 의 asset_id")
    src_group.add_argument("--image", help="원본 파일 직접 경로")

    ap.add_argument("--subject", default="ryani",
                    choices=list(SUBJECT_RECIPES.keys()),
                    help="어떤 펫 / 어떤 컷 유형인지 (default: ryani)")
    ap.add_argument("--extra", default=None,
                    help="추가 프롬프트 조각 (예: '레오가 앞발을 들고 있음. 그 발 옆에 작은 별 추가')")
    ap.add_argument("--quality", default="medium",
                    choices=["low", "medium", "high"],
                    help="이미지 품질 (medium 권장, 최종은 high)")
    ap.add_argument("--frame-time", type=float, default=None,
                    help="비디오 소스일 때 추출할 시간(초). 기본값 = 클립 중간점")
    ap.add_argument("--out", default=None,
                    help="출력 PNG 경로 (기본: data/output/decorated/<stem>__<ts>.png)")
    ap.add_argument("--dry-run", action="store_true",
                    help="프롬프트만 출력. API 호출 안 함.")
    args = ap.parse_args(argv)

    # 1) Resolve source
    if args.asset_id:
        src = resolve_asset_path(args.asset_id)
        if src is None:
            print(f"ERROR: asset_id not found in DB: {args.asset_id}",
                  file=sys.stderr)
            return 2
        stem = args.asset_id
    else:
        src = Path(args.image).expanduser().resolve()
        stem = src.stem
    if not src.exists():
        print(f"ERROR: source path missing: {src}", file=sys.stderr)
        return 2

    # 2) Build prompt
    prompt = build_prompt(args.subject, args.extra)

    print(f"Source   : {src}")
    print(f"Subject  : {args.subject}")
    print(f"Quality  : {args.quality}")
    print(f"GEN size : {GEN_SIZE}  →  target {TARGET_W}x{TARGET_H}")
    print(f"Prompt   :")
    for line in [prompt[i:i+88] for i in range(0, len(prompt), 88)]:
        print(f"  {line}")
    print()

    if args.dry_run:
        print("Dry run — no API call.")
        return 0

    # 3) Check API key
    if not os.environ.get("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY missing (set in env or .env)",
              file=sys.stderr)
        return 2

    # 4) Preprocess source → JPEG
    try:
        prepped = prepare_input(src, args.frame_time)
    except Exception as e:
        print(f"ERROR: prep failed — {e}", file=sys.stderr)
        return 3
    print(f"Prepped  : {prepped}")

    # 5) Call gpt-image-1 image.edit
    try:
        t0 = time.time()
        png_bytes = decorate(prepped, prompt, args.quality)
        dt = time.time() - t0
    except Exception as e:
        print(f"ERROR: gpt-image-1 call failed — {e}", file=sys.stderr)
        return 3
    print(f"Got      : {len(png_bytes)/1024:.0f} KB in {dt:.1f}s")

    # 6) Resize + crop to 1080x1920
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else (
        OUT_DIR / f"{stem}__{ts}.png"
    )
    to_target_9x16(png_bytes, out)
    print(f"Saved    : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
