"""
scripts/animate_hero.py
-----------------------
정지 사진 한 장을 OpenAI Sora API 로 보내서 "살짝 움직이는" mp4 로 받아오는
래퍼. 매 컷마다 쓰는 게 아니라 한 에피소드에서 hero 컷 1~3장만 골라서
켄번즈 팬/줌 대신 진짜 모션 (눈 깜빡, 고개 돌림, 꼬리 흔들기) 을 입히는 용도.

쓰는 법
-------
    # 임의의 사진 경로로
    python3 scripts/animate_hero.py \
        --image /Users/ligi/Photos/leo_window.jpg \
        --prompt "Leo slowly blinks, gentle head turn left" \
        --seconds 4

    # Episode 1 CUTS 의 asset_id 그대로 (data/agent.db 에서 자동 해석)
    python3 scripts/animate_hero.py \
        --asset-id med_2026_05_06_203421_icloud_331110de \
        --prompt "Ryani softly blinks, ear flick" \
        --seconds 4

    # 풀 해상도 (1080x1920, 비쌈)
    python3 scripts/animate_hero.py --asset-id ... --prompt "..." --pro

    # 비용 미리보기만, API 호출 X
    python3 scripts/animate_hero.py --asset-id ... --prompt "..." --dry-run

가격 (May 2026)
---------------
    sora-2     720x1280   $0.10/sec   → 4s 클립 ≈ $0.40
    sora-2-pro 1080x1920  $0.50/sec   → 4s 클립 ≈ $2.00

출력
----
    data/output/animated/<asset-stem>__<timestamp>.mp4
    --output 으로 직접 경로 지정 가능.

종료 코드
---------
    0   : 성공
    2   : 입력 오류 (파일 없음, 키 없음 등)
    3   : 생성 실패 / 타임아웃
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _require_openai():
    """Lazy import — only needed for actual API calls, not --help/--dry-run."""
    try:
        from openai import OpenAI  # noqa: WPS433
    except ImportError:
        print("ERROR: pip install openai", file=sys.stderr)
        sys.exit(2)
    return OpenAI


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "agent.db"))).resolve()
DEFAULT_OUT = ROOT / "data" / "output" / "animated"
PREP_DIR = ROOT / "data" / "tmp" / "i2v_input"     # 리사이즈된 인풋 보관

POLL_INTERVAL = 5.0
# sora-2 큐가 적체될 때 10분으로는 자주 부족 — 기본 20분으로 늘리고,
# 필요하면 SORA_POLL_TIMEOUT 환경변수로 override.
POLL_TIMEOUT = float(os.getenv("SORA_POLL_TIMEOUT", "1200"))
SUPPORTED_IMG = {".jpg", ".jpeg", ".png", ".webp"}

# May 2026 OpenAI 공식 가격
COST_PER_SEC = {"sora-2": 0.10, "sora-2-pro": 0.50}

# 카드 출력 사이즈와 입력 이미지 사이즈가 *정확히* 일치해야 함 (Sora 제약).
# 아니면 400 BadRequest: "Inpaint image must match the requested width and height"


def resolve_asset_path(asset_id: str) -> Path | None:
    """data/agent.db 의 assets 테이블에서 asset_id 의 파일 경로를 가져옴."""
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
    # render_episode_1.resolve 와 같은 매핑 — 절대 경로 안에 rianileo-agent
    # 가 들어 있으면 그 이후 부분만 ROOT 에 붙임.
    if p.is_absolute() and "rianileo-agent" in p.parts:
        idx = p.parts.index("rianileo-agent")
        p = ROOT / Path(*p.parts[idx + 1:])
    elif not p.is_absolute():
        p = ROOT / p
    return p.resolve()


def resolve_input(args: argparse.Namespace) -> Path | None:
    if args.image:
        return Path(args.image).expanduser().resolve()
    if args.asset_id:
        return resolve_asset_path(args.asset_id)
    return None


def make_default_output(img: Path) -> Path:
    DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUT / f"{img.stem}__{stamp}.mp4"


def parse_size(size: str) -> tuple[int, int]:
    w, h = size.lower().split("x")
    return int(w), int(h)


def prepare_input_image(src: Path, target_size: str) -> Path:
    """Sora 가 요구하는 정확한 (W, H) 픽셀에 맞춰 입력 이미지를 가공.

    - src 가 이미 정확한 크기면 그대로 리턴.
    - 아니면 EXIF 회전 보정 → 센터 크롭 (target aspect 에 맞춰) → 정확한
      크기로 리사이즈 → data/tmp/i2v_input/ 에 JPEG 저장 후 그 경로 리턴.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print("ERROR: pip install pillow (PIL)", file=sys.stderr)
        sys.exit(2)

    target_w, target_h = parse_size(target_size)

    with Image.open(src) as im:
        # iPhone 사진은 EXIF Orientation 태그를 자주 사용 — 명시적으로 회전
        im = ImageOps.exif_transpose(im)
        src_w, src_h = im.size

        if (src_w, src_h) == (target_w, target_h):
            print(f"      input already {src_w}x{src_h} — no resize needed")
            return src

        target_aspect = target_w / target_h
        src_aspect = src_w / src_h

        if src_aspect > target_aspect:
            # source 가 너무 wide → 좌우를 잘라냄
            new_w = int(round(src_h * target_aspect))
            left = (src_w - new_w) // 2
            box = (left, 0, left + new_w, src_h)
        else:
            # source 가 너무 tall → 위아래를 잘라냄
            new_h = int(round(src_w / target_aspect))
            top = (src_h - new_h) // 2
            box = (0, top, src_w, top + new_h)

        cropped = im.crop(box).resize((target_w, target_h), Image.LANCZOS)
        if cropped.mode != "RGB":
            cropped = cropped.convert("RGB")

        PREP_DIR.mkdir(parents=True, exist_ok=True)
        out_path = PREP_DIR / f"{src.stem}_{target_w}x{target_h}.jpg"
        cropped.save(out_path, "JPEG", quality=95)
        print(f"      resized {src_w}x{src_h} → {target_w}x{target_h}, "
              f"saved to {out_path}")
        return out_path


def _write_sidecar(path: Path, data: dict) -> None:
    """현재 메타데이터 스냅샷을 mp4 옆 .meta.json 에 박는다.
    호출마다 atomic-ish overwrite — 마지막 상태가 남음. 타임아웃/크래시 시
    video_id 라도 남아서 retry 회수 가능.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"      (warn: sidecar write failed: {e})", file=sys.stderr)


def submit_and_wait(client, *, model: str, prompt: str,
                    image_path: Path, size: str, seconds: int,
                    sidecar_path: Path | None = None,
                    meta: dict | None = None) -> str:
    """Sora 잡 제출 후 완료까지 폴링. video.id 리턴.

    OpenAI SDK 의 videos.create 는 input_reference 로 파일 자체 (PathLike
    또는 file-handle 또는 (name, fh, mime) 튜플) 를 받는다. 별도 files.create
    업로드는 필요 없음 — multipart 로 한 번에 보냄.

    sidecar_path + meta 가 넘어오면 video_id 받은 직후 / 완료 / 실패 시점에
    각각 sidecar 를 overwrite. main() 이 manage 하는 dict 를 in-place 로
    업데이트하므로 호출자도 같은 dict 를 계속 들고 갈 수 있음.
    """
    # Sora 의 input_reference 는 multipart 업로드 — (filename, file-handle,
    # mime) 튜플 형식이 가장 호환성 좋다.
    ext_to_mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".png": "image/png", ".webp": "image/webp"}
    mime = ext_to_mime[image_path.suffix.lower()]

    print(f"\n[1/2] submitting job ({model}, {size}, {seconds}s, "
          f"with {image_path.name} as {mime})...")
    with open(image_path, "rb") as fh:
        video = client.videos.create(
            model=model,
            prompt=prompt,
            input_reference=(image_path.name, fh, mime),
            size=size,
            seconds=str(seconds),
        )
    print(f"      video_id: {video.id}  status: {video.status}")

    # 잡 제출 직후 video_id 박기 — 폴링 중 죽어도 재시도 회수 가능.
    if sidecar_path is not None and meta is not None:
        meta["video_id"] = video.id
        meta["status_after_submit"] = video.status
        meta["submitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _write_sidecar(sidecar_path, meta)

    print(f"\n[2/2] polling every {POLL_INTERVAL:.0f}s "
          f"(timeout {POLL_TIMEOUT:.0f}s)...")
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > POLL_TIMEOUT:
            if sidecar_path is not None and meta is not None:
                meta["status_final"] = "timeout"
                meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                _write_sidecar(sidecar_path, meta)
            raise TimeoutError(f"timed out after {elapsed:.0f}s")
        video = client.videos.retrieve(video.id)
        print(f"      [{elapsed:6.0f}s] status: {video.status}")
        if video.status == "completed":
            if sidecar_path is not None and meta is not None:
                meta["status_final"] = "completed"
                meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                _write_sidecar(sidecar_path, meta)
            return video.id
        if video.status in {"failed", "cancelled", "error"}:
            err = getattr(video, "error", None) or "(no error message)"
            if sidecar_path is not None and meta is not None:
                meta["status_final"] = video.status
                meta["error"] = str(err)
                meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                _write_sidecar(sidecar_path, meta)
            raise RuntimeError(f"generation {video.status}: {err}")
        time.sleep(POLL_INTERVAL)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sora image-to-video wrapper for hero cuts",
    )
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--image",
                     help="source image path (jpg/png/webp)")
    src.add_argument("--asset-id",
                     help="asset_id from data/agent.db assets table")
    parser.add_argument("--prompt", required=True,
                        help="motion description in English, "
                             "e.g. 'subtle head turn, slow blink'")
    parser.add_argument("--seconds", type=int, default=4,
                        choices=[4, 8, 12],
                        help="clip duration (default 4)")
    parser.add_argument("--size", default="720x1280",
                        help="WxH, default 720x1280 (vertical Shorts).  "
                             "Use 1080x1920 with --model sora-2-pro for HQ.")
    parser.add_argument("--model", default="sora-2",
                        choices=["sora-2", "sora-2-pro"])
    parser.add_argument("--pro", action="store_true",
                        help="shortcut: --model sora-2-pro --size 1080x1920")
    parser.add_argument("--output",
                        help="output mp4 path "
                             "(default data/output/animated/<stem>__<ts>.mp4)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print plan + cost, no API call")
    args = parser.parse_args()

    if args.pro:
        args.model = "sora-2-pro"
        args.size = "1080x1920"

    img = resolve_input(args)
    if img is None or not img.exists():
        print(f"ERROR: image not found (asset_id={args.asset_id}, "
              f"image={args.image})", file=sys.stderr)
        return 2
    if img.suffix.lower() not in SUPPORTED_IMG:
        print(f"ERROR: unsupported format {img.suffix}.  "
              f"Sora accepts {sorted(SUPPORTED_IMG)}.  "
              f"HEIC 파일은 먼저 jpg/png 으로 변환해주세요.", file=sys.stderr)
        return 2

    out = Path(args.output).expanduser().resolve() if args.output \
          else make_default_output(img)
    out.parent.mkdir(parents=True, exist_ok=True)

    rate = COST_PER_SEC[args.model]
    cost = args.seconds * rate

    print("=" * 60)
    print(f"image    : {img}")
    print(f"prompt   : {args.prompt}")
    print(f"model    : {args.model}")
    print(f"size     : {args.size}")
    print(f"seconds  : {args.seconds}")
    print(f"output   : {out}")
    print(f"est cost : ~${cost:.2f}")
    print("=" * 60)

    if args.dry_run:
        print("(dry-run — no API call)")
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set (check .env)", file=sys.stderr)
        return 2

    OpenAI = _require_openai()
    client = OpenAI(api_key=api_key)

    # 입력 이미지 크기 보정 — Sora 는 output 사이즈와 정확히 같은 픽셀 요구.
    print(f"\n[0/2] preparing input image (target {args.size})...")
    prepared_img = prepare_input_image(img, args.size)

    # 호출 전 사이드카 메타 준비. mp4 옆에 <out>.meta.json 으로 박힘.
    # video_id 받자마자/완료/실패/타임아웃 시점에 submit_and_wait 가
    # in-place 업데이트하므로, 어디서 죽어도 마지막 상태가 남는다.
    sidecar_path = out.with_suffix(out.suffix + ".meta.json")
    meta = {
        "schema_version": 1,
        "model": args.model,
        "size": args.size,
        "seconds": args.seconds,
        "prompt": args.prompt,
        "image_input": str(img),
        "image_prepared": str(prepared_img) if prepared_img != img else None,
        "asset_id": args.asset_id,
        "output_mp4": str(out),
        "cost_estimate_usd": cost,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    _write_sidecar(sidecar_path, meta)

    try:
        video_id = submit_and_wait(
            client,
            model=args.model,
            prompt=args.prompt,
            image_path=prepared_img,
            size=args.size,
            seconds=args.seconds,
            sidecar_path=sidecar_path,
            meta=meta,
        )
    except (TimeoutError, RuntimeError) as e:
        print(f"\n      !! {e}", file=sys.stderr)
        print(f"      meta saved to {sidecar_path}", file=sys.stderr)
        return 3

    print(f"\ndownloading mp4...")
    content = client.videos.download_content(video_id, variant="video")
    with open(out, "wb") as f:
        # content is a streamed response — write all bytes
        if hasattr(content, "read"):
            f.write(content.read())
        else:
            # fallback: iterate
            for chunk in content.iter_bytes():
                f.write(chunk)

    # 다운로드 완료 — sidecar 에 mp4 경로/완료 시각 마감.
    meta["downloaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta["mp4_bytes"] = out.stat().st_size if out.exists() else None
    _write_sidecar(sidecar_path, meta)

    print(f"\n  ✓ saved : {out}")
    print(f"  ✓ meta  : {sidecar_path}")
    print(f"  ✓ cost  : ~${cost:.2f}")
    print(f"\n다음 단계: 이 mp4 를 render_episode_1.py 의 해당 Cut 에서 "
          f"정지 사진 대신 사용하려면 Cut 데이터클래스에 클립 경로 필드를 "
          f"추가하거나 잠시 수동으로 합성해서 확인 가능.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
