"""MV 타이틀 카드 PNG 컴포저 (make_mv `title_image`용, 재사용).

drawtext 테마로는 손글씨·부드러운 섀도우·로워서드 배치를 못 한다 → PIL로 풀프레임 RGBA
카드를 그려 make_mv(title_image=...)로 인트로에 페이드 오버레이한다
(notes/mv_agent_feedback_log.md §5). 곡 무드에 맞춰 폰트/색/위치를 고른다:
귀엽고 따뜻한 곡 = 손글씨(나눔펜)+크림/감빛, 하단 로워서드. 감꼭지 MV에서 검증됨.

    from scripts.mv_title_card import make_title_card
    make_title_card("감꼭지", out, kicker="♪ 랴니레오 오리지널 M/V",
                    credit="작곡 라레수 · 작사 함미하비", mood="warm", vpos=0.66)
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

_FONTS = Path.home() / "Library/Fonts"
FONT = {
    "pen":   _FONTS / "NanumPenScript-Regular.ttf",   # 손글씨 — 귀엽고 따뜻한 곡
    "bold":  _FONTS / "Pretendard-Bold.otf",          # 모던/정제
    "black": _FONTS / "Pretendard-Black.otf",          # 펀치/팝
}
# 무드별 팔레트: (title, kicker, shadow/outline)  RGBA
MOODS = {
    "warm":  dict(title=(255, 243, 223, 255), kicker=(242, 165, 76, 255), shadow=(60, 34, 20, 255), font="pen"),
    "cream": dict(title=(255, 241, 220, 255), kicker=(255, 255, 255, 255), shadow=(40, 30, 20, 255), font="pen"),
    "pop":   dict(title=(255, 225, 77, 255),  kicker=(0, 212, 255, 255),   shadow=(10, 10, 20, 255), font="black"),
    "mono":  dict(title=(245, 245, 245, 255), kicker=(200, 200, 200, 255), shadow=(10, 10, 10, 255), font="bold"),
}


def make_title_card(title, out, *, kicker="", credit="", mood="warm",
                    vpos=0.66, title_px=230, W=1080, H=1920):
    """풀프레임 RGBA 타이틀 PNG. vpos=세로 중심(0=위,1=아래; 하단 로워서드=~0.66,
    자막 밴드 ~0.88과 안 겹침). title_px=타이틀 글자 크기."""
    m = MOODS.get(mood, MOODS["warm"])
    f_title = ImageFont.truetype(str(FONT[m["font"]]), title_px)
    f_kick = ImageFont.truetype(str(FONT["bold"]), 46)
    f_cred = ImageFont.truetype(str(FONT["bold"]), 44)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def centered(text, font, cy, fill, sw, shadow_xy):
        bb = d.textbbox((0, 0), text, font=font, stroke_width=sw)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = (W - tw) / 2 - bb[0]
        y = cy - th / 2 - bb[1]
        sc = (m["shadow"][0], m["shadow"][1], m["shadow"][2], 150)
        d.text((x + shadow_xy[0], y + shadow_xy[1]), text, font=font, fill=sc, stroke_width=sw, stroke_fill=sc)
        d.text((x, y), text, font=font, fill=fill, stroke_width=sw, stroke_fill=m["shadow"])

    cy = int(H * vpos)
    if kicker:
        centered(kicker, f_kick, cy - 150, m["kicker"], 3, (3, 4))
    centered(title, f_title, cy, m["title"], 10, (7, 9))
    if credit:
        centered(credit, f_cred, cy + 165, m["title"], 3, (3, 4))
    img.save(out)
    return out


if __name__ == "__main__":
    import sys
    make_title_card(sys.argv[1] if len(sys.argv) > 1 else "감꼭지",
                    "/tmp/mv_title_demo.png", kicker="♪ 랴니레오 오리지널 M/V",
                    credit="작곡 라레수 · 작사 함미하비", mood="warm")
    print("saved /tmp/mv_title_demo.png")
