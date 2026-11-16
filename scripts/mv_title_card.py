"""MV 타이틀 카드 PNG 컴포저 + ★자동 무드 선택 (make_mv `title_image`용, 재사용).

drawtext 테마로는 손글씨·부드러운 섀도우·로워서드·스크립트 서체를 못 한다 → PIL로 풀프레임 RGBA
카드를 그려 make_mv(title_image=...)로 인트로에 페이드 오버레이한다 (notes/mv_agent_feedback_log.md §5).

★에이전트화 (PD 2026-11-16: "타이틀도 에이전트화해야 해 — 항상 마지막에 부탁하잖아"):
MV 빌더는 타이틀을 수동으로 지정하지 말고 **곡 스타일/무드 문자열에서 자동으로** 무드·서체·위치를
골라야 한다 → `auto_card(title, out, style=<곡 style 문자열>, kicker=..., credit=...)`.
무드별: soul/jazz/ballad=우아한 골드+영문 스크립트 키커(중앙), acoustic/warm=손글씨 크림(로워서드),
pop/dance=블랙 네온, halloween=보라/주황.

    from scripts.mv_title_card import auto_card
    auto_card("너는 내 이름도", out, style="neo-soul R&B ballad ...",
              kicker="You Don't Know My Name", credit="작곡 라레수 · 작사 함미하비")
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

_FONTS = Path.home() / "Library/Fonts"
_SYS = Path("/System/Library/Fonts/Supplemental")
FONT = {
    "pen":   _FONTS / "NanumPenScript-Regular.ttf",   # 손글씨 — 귀엽고 따뜻한 곡
    "bold":  _FONTS / "Pretendard-Bold.otf",          # 모던/정제
    "black": _FONTS / "Pretendard-Black.otf",         # 펀치/팝
    "med":   _FONTS / "Pretendard-Medium.otf",        # 정제/우아 (한글 타이틀)
}
# 우아한 라틴 스크립트/세리프 — 영문 키커 전용(한글 렌더 불가)
SCRIPT = {
    "snell": _SYS / "SnellRoundhand.ttc",             # 로맨틱 스크립트 — soul/발라드
    "didot": _SYS / "Didot.ttc",                      # 우아한 세리프
    "georgia_it": _SYS / "Georgia Italic.ttf",
}
# 무드별 팔레트 + 기본 서체/키커서체/위치
MOODS = {
    "warm":  dict(title=(255, 243, 223, 255), kicker=(242, 165, 76, 255), shadow=(60, 34, 20, 255), font="pen",  kfont="bold",  vpos=0.66),
    "cream": dict(title=(255, 241, 220, 255), kicker=(255, 255, 255, 255), shadow=(40, 30, 20, 255), font="pen",  kfont="bold",  vpos=0.66),
    "pop":   dict(title=(255, 225, 77, 255),  kicker=(0, 212, 255, 255),   shadow=(10, 10, 20, 255), font="black", kfont="black", vpos=0.5),
    "mono":  dict(title=(245, 245, 245, 255), kicker=(200, 200, 200, 255), shadow=(10, 10, 10, 255), font="bold",  kfont="bold",  vpos=0.5),
    # neo-soul / retro romance — refined warm gold title + elegant English SCRIPT kicker, centered
    "soul":  dict(title=(234, 203, 140, 255), kicker=(241, 227, 196, 255), shadow=(24, 15, 8, 255),  font="med",  kfont="snell", vpos=0.52),
}

# 곡 style/무드 문자열 → 무드 키워드 (자동 선택)
_MOOD_KW = {
    "soul": ("soul", "r&b", "rnb", "rhythm and blues", "neo-soul", "neosoul", "jazz", "blues", "ballad", "motown", "발라드", "소울"),
    "warm": ("acoustic", "folk", "cozy", "lofi", "lo-fi", "country", "gentle", "lullaby", "warm", "어쿠스틱", "포크"),
    "pop":  ("pop", "dance", "edm", "club", "hip hop", "hiphop", "hip-hop", "hype", "electro", "trap", "house", "k-pop", "kpop", "댄스", "힙합"),
    "halloween": ("halloween", "spooky", "horror", "haunted", "할로윈"),
}


def auto_mood(style: str | None) -> str:
    """곡 style/장르 문자열에서 타이틀 무드를 고른다 (없으면 warm)."""
    s = (style or "").lower()
    for mood, kws in _MOOD_KW.items():
        if any(k in s for k in kws):
            return mood
    return "warm"


def _load(path, px):
    """truetype 로더 — .ttc 컬렉션(index 0) 허용."""
    try:
        return ImageFont.truetype(str(path), px)
    except Exception:
        return ImageFont.truetype(str(path), px, index=0)


def _font_path(name):
    return SCRIPT[name] if name in SCRIPT else FONT.get(name, FONT["bold"])


def make_title_card(title, out, *, kicker="", credit="", mood="warm",
                    vpos=None, title_px=None, W=1080, H=1920,
                    kicker_font=None, kicker_px=None):
    """풀프레임 RGBA 타이틀 PNG. mood가 서체/색/위치 기본값을 준다; 인자로 오버라이드 가능.
    kicker_font = FONT/SCRIPT 키 이름 또는 경로 (영문 키커용 스크립트 서체)."""
    m = MOODS.get(mood, MOODS["warm"])
    vpos = m.get("vpos", 0.66) if vpos is None else vpos
    title_px = title_px or (200 if mood == "soul" else 230)
    kicker_px = kicker_px or (60 if mood == "soul" else 46)
    kfont_name = kicker_font or m.get("kfont", "bold")
    f_title = _load(FONT[m["font"]], title_px)
    f_kick = _load(_font_path(kfont_name), kicker_px)
    f_cred = _load(FONT["bold"], 44)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # auto-fit: shrink the title font until it fits within the frame width (margin),
    # so long titles never overflow the edges (agentized — any title length is safe).
    maxw = W * 0.86
    while title_px > 70 and d.textlength(title, font=f_title) > maxw:
        title_px -= 6
        f_title = _load(FONT[m["font"]], title_px)

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
        centered(kicker, f_kick, cy - (165 if mood == "soul" else 150), m["kicker"], 2, (3, 4))
    centered(title, f_title, cy, m["title"], 8 if mood == "soul" else 10, (6, 8))
    if credit:
        centered(credit, f_cred, cy + 155, m["title"], 3, (3, 4))
    img.save(out)
    return out


def make_retro_card(title, out, *, kicker="", credit="", W=1080, H=1920, vpos=0.5,
                    title_font=None, kicker_font=None):
    """★경양식/노포 레트로 EMBLEM 타이틀 (PD 2026-11-16: "허접하지 않게, 그래픽 요소·경양식 느낌").
    을지로체(옛 간판 서체)로 타이틀 + 금색 이중 프레임 + 다이아 장식 디바이더 = '디자인된 간판'.
    """
    F_TITLE = title_font or str(_FONTS / "BMEULJIRO.otf")      # 을지로체 — 옛 간판/경양식
    F_KICK = kicker_font or str(SCRIPT["didot"])              # 우아한 세리프 (영문 키커)
    F_CRED = "/System/Library/Fonts/AppleMyungjo.ttf"        # 명조 세리프 — 레트로 크레딧(경양식 톤)
    GOLD = (205, 167, 102, 255); GOLD_SOFT = (224, 196, 140, 255)
    CREAM = (245, 234, 208, 255); PANEL = (26, 17, 10, 180); BORDER = (196, 158, 92, 240)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_title = _load(F_TITLE, 168); f_kick = _load(F_KICK, 60); f_cred = _load(F_CRED, 42)
    # fit title width
    px = 168
    while px > 80 and d.textlength(title, font=f_title) > W * 0.70:
        px -= 6; f_title = _load(F_TITLE, px)

    def wh(txt, f, sw=0):
        b = d.textbbox((0, 0), txt, font=f, stroke_width=sw); return b[2]-b[0], b[3]-b[1], b
    kw_, kh_, _ = wh(kicker, f_kick) if kicker else (0, 0, None)
    tw_, th_, tb = wh(title, f_title, 2)
    cw_, ch_, _ = wh(credit, f_cred) if credit else (0, 0, None)
    content_w = max(kw_, tw_, cw_)
    pad_x, pad_y, gap = 90, 70, 34
    pw = min(W - 80, content_w + pad_x * 2)
    ph = pad_y*2 + (kh_+gap if kicker else 0) + 40 + gap + th_ + (gap+ch_ if credit else 0)
    cx, cy = W/2, H*vpos
    x0, y0, x1, y1 = cx-pw/2, cy-ph/2, cx+pw/2, cy+ph/2
    # panel + double gold frame
    d.rounded_rectangle([x0, y0, x1, y1], radius=18, fill=PANEL)
    d.rounded_rectangle([x0, y0, x1, y1], radius=18, outline=BORDER, width=5)
    d.rounded_rectangle([x0+14, y0+14, x1-14, y1-14], radius=10, outline=(BORDER[0], BORDER[1], BORDER[2], 170), width=2)
    # corner diamonds
    for (ox, oy) in [(x0+14, y0+14), (x1-14, y0+14), (x0+14, y1-14), (x1-14, y1-14)]:
        d.polygon([(ox, oy-7), (ox+7, oy), (ox, oy+7), (ox-7, oy)], fill=GOLD)

    def ctext(txt, f, yc, fill, sw=0):
        b = d.textbbox((0, 0), txt, font=f, stroke_width=sw)
        x = cx - (b[2]-b[0])/2 - b[0]; y = yc - (b[3]-b[1])/2 - b[1]
        d.text((x+3, y+4), txt, font=f, fill=(12, 8, 4, 150), stroke_width=sw, stroke_fill=(12, 8, 4, 150))
        d.text((x, y), txt, font=f, fill=fill, stroke_width=sw, stroke_fill=(40, 26, 12, 255))
    yy = y0 + pad_y
    if kicker:
        ctext(kicker, f_kick, yy + kh_/2, GOLD_SOFT); yy += kh_ + gap
    # ornament divider: ─── ◆ ───
    dyy = yy + 20; half = tw_*0.36
    d.line([cx-half, dyy, cx-26, dyy], fill=GOLD, width=3)
    d.line([cx+26, dyy, cx+half, dyy], fill=GOLD, width=3)
    d.polygon([(cx, dyy-9), (cx+11, dyy), (cx, dyy+9), (cx-11, dyy)], fill=GOLD)
    yy += 40 + gap
    ctext(title, f_title, yy + th_/2, CREAM, sw=2); yy += th_
    if credit:
        yy += gap; ctext(credit, f_cred, yy + ch_/2, GOLD_SOFT)
    img.save(out)
    return out


def auto_card(title, out, *, style="", kicker="", credit="", **kw):
    """★에이전트 진입점: 곡 style 문자열에서 무드를 자동 선택해 카드를 만든다.
    kicker 미지정 시 채널 기본 키커. MV 빌더는 항상 이걸 호출해 타이틀을 자동 생성한다."""
    mood = auto_mood(style)
    if not kicker:
        kicker = "♪ 랴니레오 오리지널 M/V"
    return make_title_card(title, out, kicker=kicker, credit=credit, mood=mood, **kw)


if __name__ == "__main__":
    import sys
    auto_card(sys.argv[1] if len(sys.argv) > 1 else "너는 내 이름도",
              "/tmp/mv_title_demo.png", style="neo-soul R&B ballad",
              kicker="You Don't Know My Name", credit="작곡 라레수 · 작사 함미하비")
    print("saved /tmp/mv_title_demo.png (mood auto-picked)")
