"""Refined M/V title card via PIL (quality pass — PD: drawtext version looked tacky). Soft glow
(no hard black stroke), a gradient scrim for legibility, restrained cream/gold palette, letter-
spaced kicker, thin hairline rule. Renders a transparent-ready PNG composited over a real frame so
PD can judge it in context. Two directions: 'cozy' (warm cream+gold) and 'moody' (white+plum glow)."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1080, 1920
FD = Path.home() / "Library/Fonts"
def font(name, sz): return ImageFont.truetype(str(FD / name), sz)


def _spaced(s, n=1):
    return (" " * n).join(list(s))


def _text_center(draw, y, text, fnt, fill, img=None, glow=None, glow_px=12):
    w = draw.textlength(text, font=fnt)
    x = (W - w) / 2
    if glow and img is not None:
        gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(gl).text((x, y), text, font=fnt, fill=glow)
        gl = gl.filter(ImageFilter.GaussianBlur(glow_px))
        img.alpha_composite(gl)
    draw.text((x, y), text, font=fnt, fill=fill)
    return x, w


def card(theme, title, out, bg_frame=None, credit="작곡 라레수 · 작사 함미하비"):
    if theme == "cozy":
        kicker_c, title_c, accent, glow = (232, 201, 138, 255), (247, 239, 224, 255), (232, 201, 138, 255), (120, 60, 20, 180)
        title_font = font("Pretendard-ExtraBold.otf", 128)
    else:  # moody
        kicker_c, title_c, accent, glow = (210, 190, 230, 255), (245, 245, 250, 255), (176, 130, 220, 255), (90, 40, 140, 200)
        title_font = font("Pretendard-Bold.otf", 126)
    kick_font = font("Pretendard-SemiBold.otf", 34)
    cred_font = font("Pretendard-Medium.otf", 34)

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # top gradient scrim (dark → transparent) for legibility + a "designed" band
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sp = scrim.load()
    band = int(H * 0.46)
    for y in range(band):
        a = int(150 * (1 - y / band) ** 1.4)
        for x in range(0, W, 1):
            sp[x, y] = (10, 8, 16, a)
    img.alpha_composite(scrim)
    d = ImageDraw.Draw(img)

    y0 = int(H * 0.15)
    _text_center(d, y0, _spaced("RYANI × LEO · HALLOWEEN", 1), kick_font, kicker_c)
    _text_center(d, y0 + 58, title, title_font, title_c, img=img, glow=glow, glow_px=16)
    # thin hairline rule with centered diamond
    ry = y0 + 58 + 150
    d.line([(W/2 - 150, ry), (W/2 - 14, ry)], fill=accent, width=2)
    d.line([(W/2 + 14, ry), (W/2 + 150, ry)], fill=accent, width=2)
    d.polygon([(W/2, ry-7), (W/2+9, ry), (W/2, ry+7), (W/2-9, ry)], fill=accent)
    _text_center(d, ry + 26, credit, cred_font, (235, 235, 235, 235))

    if bg_frame and Path(bg_frame).exists():
        base = Image.open(bg_frame).convert("RGBA").resize((W, H))
        base.alpha_composite(img)
        base.convert("RGB").save(out, quality=92)
    else:
        img.save(out)
    print("  →", out)


if __name__ == "__main__":
    out = Path("data/tmp/mv/topics"); bg = out / "frames/title_bg.jpg"
    card("cozy",  "오늘 밤 주인공", out / "title_cozy.jpg",  bg_frame=bg)
    card("moody", "오늘 밤 주인공", out / "title_moody.jpg", bg_frame=bg)
