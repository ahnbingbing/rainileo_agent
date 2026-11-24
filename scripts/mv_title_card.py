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
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

_FONTS = Path.home() / "Library/Fonts"
_SYS = Path("/System/Library/Fonts/Supplemental")
FONT = {
    "pen":   _FONTS / "NanumPenScript-Regular.ttf",   # 손글씨 — 귀엽고 따뜻한 곡
    "bold":  _FONTS / "Pretendard-Bold.otf",          # 모던/정제
    "black": _FONTS / "Pretendard-Black.otf",         # 펀치/팝
    "med":   _FONTS / "Pretendard-Medium.otf",        # 정제/우아 (한글 타이틀)
    "jua":   _FONTS / "BMJUA_otf.otf",                # 배민 주아 — 둥글둥글 레트로 팝(미드센츄리 모드)
    "dohyeon": _FONTS / "BMDOHYEON_otf.otf",          # 도현 — 굵은 라운드 펀치
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
        d.text((x + shadow_xy[0], y + shadow_xy[1]), text, font=font, fill=sc)
        # Uniform border via a manual 8-dir + ring stamp instead of PIL stroke_fill:
        # with brush/script fonts (NanumPen) a neighbouring glyph's fill can overpaint a
        # THIN stroke's outline, dropping its border (e.g. the ㅡ in 끈 鈍). A full ring of
        # offset stamps guarantees every stroke — thin or thick — gets an even outline.
        if sw > 0:
            offs = set()
            for ang in range(0, 360, 30):
                offs.add((round(sw * math.cos(math.radians(ang))), round(sw * math.sin(math.radians(ang)))))
            for r in (sw, max(1, sw // 2)):
                for dx, dy in ((-r, 0), (r, 0), (0, -r), (0, r), (-r, -r), (r, -r), (-r, r), (r, r)):
                    offs.add((dx, dy))
            for dx, dy in offs:
                d.text((x + dx, y + dy), text, font=font, fill=m["shadow"])
        d.text((x, y), text, font=font, fill=fill)

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


def make_y2k_card(title, out, *, kicker="", credit="", W=1080, H=1920, vpos=0.19):
    """★Y2K 크롬 EMBLEM 타이틀 (PD 2026-11-18, 브릴리언 몸값 레오): 힙합/Y2K 곡은 레트로-골드가 아니라
    Y2K 크롬 컨셉. 인디고 글로시 패널 + 크롬 실버 더블 프레임 + 코너 별 스파클 + 크롬 메탈릭 타이틀
    (실버→블루→골드 세로 그라데). ★vpos 기본 상단(0.19) — 피사체 얼굴 안 가리게. ★디자인 패널이라
    가독성 확보(맨글자=허접·블링떡칠=지저분, 둘 다 금지). 레트로 카드(make_retro_card)의 Y2K판."""
    BLK, SB = FONT["black"], FONT["bold"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    f_t = _load(BLK, 146)
    while d.textlength(title, font=f_t) > W * 0.70 and f_t.size > 80:
        f_t = _load(BLK, f_t.size - 4)
    f_k, f_c = _load(SB, 34), _load(SB, 28)
    tw = d.textlength(title, font=f_t)
    pw = min(W - 80, int(max(tw, d.textlength(kicker, font=f_k)) + 160)); ph = 420
    cx, cy = W / 2, H * vpos
    x0, y0, x1, y1 = cx - pw/2, cy - ph/2, cx + pw/2, cy + ph/2
    pan = Image.new("RGBA", (int(pw), int(ph)), (0, 0, 0, 0)); pdd = ImageDraw.Draw(pan)
    for yy in range(int(ph)):
        pdd.line([(0, yy), (pw, yy)], fill=(int(18+24*yy/ph), int(22-8*yy/ph), int(58-18*yy/ph), 234))
    m = Image.new("L", (int(pw), int(ph)), 0); ImageDraw.Draw(m).rounded_rectangle([0, 0, pw-1, ph-1], radius=26, fill=255)
    img.paste(pan, (int(x0), int(y0)), m)
    d.rounded_rectangle([x0, y0, x1, y1], radius=26, outline=(214, 228, 255, 245), width=5)
    d.rounded_rectangle([x0+12, y0+12, x1-12, y1-12], radius=18, outline=(120, 200, 255, 150), width=2)
    def star(ox, oy, s, col):
        d.polygon([(ox, oy-s), (ox+s*.28, oy-s*.28), (ox+s, oy), (ox+s*.28, oy+s*.28), (ox, oy+s),
                   (ox-s*.28, oy+s*.28), (ox-s, oy), (ox-s*.28, oy-s*.28)], fill=col)
    for ox, oy in [(x0+12, y0+12), (x1-12, y0+12), (x0+12, y1-12), (x1-12, y1-12)]:
        star(ox, oy, 11, (225, 240, 255, 255))
    d.text((cx - d.textlength(kicker, font=f_k)/2, y0+54), kicker, font=f_k, fill=(150, 215, 255, 255))
    dy = y0 + 108; hl = tw * 0.33
    d.line([cx-hl, dy, cx-22, dy], fill=(190, 225, 255, 230), width=3)
    d.line([cx+22, dy, cx+hl, dy], fill=(190, 225, 255, 230), width=3); star(cx, dy, 9, (150, 215, 255, 255))
    tb = d.textbbox((0, 0), title, font=f_t, stroke_width=4); bw, bh = tb[2]-tb[0], tb[3]-tb[1]
    tmask = Image.new("L", (bw+8, bh+8), 0); ImageDraw.Draw(tmask).text((-tb[0]+4, -tb[1]+4), title, font=f_t, fill=255)
    grad = Image.new("RGBA", (bw+8, bh+8), (0, 0, 0, 0)); gd = ImageDraw.Draw(grad)
    stops = [(0, (242, 247, 255)), (.32, (170, 198, 235)), (.5, (72, 98, 152)), (.62, (225, 236, 255)), (.8, (238, 214, 120)), (1, (210, 170, 70))]
    for yy in range(bh+8):
        t = yy/(bh+8); col = stops[-1][1]
        for i in range(len(stops)-1):
            a, ca = stops[i]; b2, cb = stops[i+1]
            if a <= t <= b2:
                f = (t-a)/(b2-a); col = tuple(int(ca[k]+(cb[k]-ca[k])*f) for k in range(3)); break
        gd.line([(0, yy), (bw+8, yy)], fill=col+(255,))
    grad.putalpha(tmask)
    tx, ty = int(cx - bw/2), int(dy + 34)
    d.text((tx-tb[0]+4, ty-tb[1]+4), title, font=f_t, fill=(10, 16, 40, 255), stroke_width=4, stroke_fill=(10, 16, 40, 255))
    img.alpha_composite(grad, (tx, ty))
    if credit:
        d.text((cx - d.textlength(credit, font=f_c)/2, y1-58), credit, font=f_c, fill=(205, 222, 245, 255))
    img.save(out); return out


# Y2K/힙합 플렉스 → 크롬 카드로 라우팅 (레트로-골드 아님)
_Y2K_KW = ("y2k", "2000s", "chrome", "boy band", "boyband", "nsync", "n sync", "bling", "힙합 플렉스", "크롬")


def make_midcentury_card(title, out, *, kicker="", credit="", subtitle="",
                         W=1080, H=1920, vpos=0.20, title_font=None):
    """★미드센츄리 모드 타이틀 (PD 2026-11-19: MV의 체커보드/머스터드·틸 룩과 한 세트).
    둥근 레트로 팝 한글 타이포(주아체) + 2단 체커보드 트림 + 머스터드/틸/크림 팔레트 + 모드 선버스트.
    ★위치는 상단(vpos≈0.20) 기본 — 펫은 보통 화면 중앙·하단에 있으니 타이틀이 펫을 가리면 안 된다
    (PD 반복 지적: '랴니 레오를 가리잖아, 위로 올려'). make_mv(title_image=)로 인트로 오버레이."""
    F_TITLE = title_font or str(FONT["jua"])
    F_SUB = str(FONT["dohyeon"]); F_KICK = str(FONT["bold"]); F_CRED = str(FONT["bold"])
    MUSTARD = (214, 160, 58, 255); MUST_D = (173, 122, 33, 255)
    TEAL = (72, 132, 122, 255); CREAM = (247, 238, 214, 255)
    DARK = (46, 33, 20, 255)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f_title = _load(F_TITLE, 190); f_sub = _load(F_SUB, 72); f_kick = _load(F_KICK, 50); f_cred = _load(F_CRED, 40)
    # fit title
    px = 190
    while px > 90 and d.textlength(title, font=f_title) > W * 0.78:
        px -= 6; f_title = _load(F_TITLE, px)

    def wh(txt, f, sw=0):
        b = d.textbbox((0, 0), txt, font=f, stroke_width=sw); return b[2]-b[0], b[3]-b[1]
    kw_, kh_ = wh(kicker, f_kick) if kicker else (0, 0)
    tw_, th_ = wh(title, f_title)
    sw_, sh_ = wh(subtitle, f_sub) if subtitle else (0, 0)
    cw_, ch_ = wh(credit, f_cred) if credit else (0, 0)
    pad_x, pad_y, gap, strip = 70, 50, 26, 34
    content_w = max(kw_, tw_, sw_, cw_)
    pw = min(W - 56, content_w + pad_x * 2)
    inner = pad_y*2 + (kh_+gap if kicker else 0) + th_ + (gap+sh_ if subtitle else 0) + (gap+ch_ if credit else 0)
    ph = inner + strip*2
    cx, cy = W/2, H*vpos
    x0, y0, x1, y1 = cx-pw/2, cy-ph/2, cx+pw/2, cy+ph/2

    # cream rounded panel + teal frame
    d.rounded_rectangle([x0, y0, x1, y1], radius=26, fill=CREAM)
    d.rounded_rectangle([x0, y0, x1, y1], radius=26, outline=TEAL, width=6)

    # 2-row checkerboard strips (top & bottom) in mustard/cream
    sq = strip // 2
    def checker(y_top):
        for row in range(2):
            yy = y_top + row*sq
            for i, xx in enumerate(range(int(x0)+8, int(x1)-8, sq)):
                if (i + row) % 2 == 0:
                    d.rectangle([xx, yy, min(xx+sq, x1-8), yy+sq], fill=MUSTARD)
    checker(int(y0)+8); checker(int(y1)-8-strip)

    # mod sunburst accent above the panel
    import math
    sb_cx, sb_cy, r0, r1 = cx, y0-6, 14, 40
    for a in range(0, 360, 30):
        rad = math.radians(a)
        d.line([(sb_cx+math.cos(rad)*r0, sb_cy+math.sin(rad)*r0),
                (sb_cx+math.cos(rad)*r1, sb_cy+math.sin(rad)*r1)], fill=MUSTARD, width=6)
    d.ellipse([sb_cx-12, sb_cy-12, sb_cx+12, sb_cy+12], fill=TEAL)

    def ctext(txt, f, yc, fill, sw=0, sfill=None):
        b = d.textbbox((0, 0), txt, font=f, stroke_width=sw)
        x = cx - (b[2]-b[0])/2 - b[0]; y = yc - (b[3]-b[1])/2 - b[1]
        d.text((x, y), txt, font=f, fill=fill, stroke_width=sw, stroke_fill=sfill)

    y = y0 + strip + pad_y
    if kicker:
        ctext(kicker, f_kick, y + kh_/2, TEAL); y += kh_ + gap
    ctext(title, f_title, y + th_/2, MUSTARD, sw=5, sfill=DARK); y += th_ + gap
    if subtitle:
        ctext(subtitle, f_sub, y + sh_/2, TEAL, sw=3, sfill=CREAM); y += sh_ + gap
    if credit:
        ctext(credit, f_cred, y + ch_/2, (120, 96, 60, 255))

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return Path(out)


# mid-century / mod / retro-pop 키워드 → 미드센츄리 카드
def make_snow_card(title, out, *, kicker="", credit="", W=1080, H=1920, vpos=0.2,
                   title_px=200, snow_phase=0.0):
    """★눈/크리스마스 캐롤 무드 — 글자 자체를 '눈사람처럼' 아트화(PD 2026-11-23).
    둥근 서체(주아) + 얼음빛 글자 + 각 획 위에 소복이 쌓인 흰 눈 '모자' + 통통한 눈덩이 + 흩날리는 눈송이.
    눈 모자 = 글자 마스크를 위로 올려 부풀린(팽창) 흰 레이어를 글자 뒤에 깔아 윗면만 삐죽 나오게 하는 기법.
    `snow_phase`(0..1) = 흩날리는 눈송이가 아래로 내려오는 애니메이션 위상 — make_snow_title_anim이 프레임마다
    올려 호출해 눈이 떨어지는 오버레이 영상을 만든다(글자/눈모자/눈사람은 고정, 눈송이만 낙하)."""
    from PIL import ImageFilter
    f_title = _load(FONT["jua"], title_px)            # 둥글둥글 = 눈사람 느낌
    f_kick = _load(FONT["jua"], max(30, int(title_px * 0.24)))  # 키커도 같은 둥근 서체로 통일(제너릭 고딕 금지)
    f_cred = _load(FONT["jua"], max(28, int(title_px * 0.21)))
    ice, ice_edge, blue_sh = (238, 248, 255, 255), (120, 170, 220, 255), (40, 70, 120, 150)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    maxw = W * 0.84
    tp = title_px
    while tp > 70 and d.textlength(title, font=f_title) > maxw:
        tp -= 6
        f_title = _load(FONT["jua"], tp)
    bb = d.textbbox((0, 0), title, font=f_title, stroke_width=12)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (W - tw) / 2 - bb[0]
    cy = int(H * vpos)
    y = cy - th / 2 - bb[1]
    cap = max(8, tp // 14)                             # 눈 쌓임 높이

    # 1) 눈 '모자' 레이어 — 글자를 흰색으로 그려 팽창시키고 위로 올려, 글자 뒤에 깔아 윗면만 삐죽.
    snow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(snow)
    for dx in range(-cap, cap + 1):                    # 가로로 두껍게 = 획 윗면 전체 덮기
        sd.text((x + dx, y - cap), title, font=f_title, fill=(255, 255, 255, 255))
    snow = snow.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(3))
    img.alpha_composite(snow)

    # 2) 얼음빛 본체 글자 (파란 그림자 + 파란 외곽 + 흰-얼음 채움) — 눈 모자 위에 올려 아랫부분 덮음
    d.text((x + 6, y + 8), title, font=f_title, fill=blue_sh)            # drop shadow
    for ang in range(0, 360, 24):                                        # 균일 외곽선(링 스탬프)
        dx, dy = round(10 * math.cos(math.radians(ang))), round(10 * math.sin(math.radians(ang)))
        d.text((x + dx, y + dy), title, font=f_title, fill=ice_edge)
    d.text((x, y), title, font=f_title, fill=ice)

    # 3) 통통한 눈덩이 — 글자 윗라인 따라 흰 덩어리 몇 개 + 하이라이트
    import hashlib
    seed = int(hashlib.md5(title.encode()).hexdigest(), 16)
    for i in range(5):
        bx = x + tw * (0.12 + 0.19 * i) + ((seed >> (i * 3)) % 20 - 10)
        by = y - cap + 2
        r = cap * (0.9 + ((seed >> i) % 5) / 10)
        d.ellipse([bx - r, by - r, bx + r, by + r], fill=(255, 255, 255, 255))
        d.ellipse([bx - r * 0.4, by - r * 0.5, bx + r * 0.1, by], fill=(205, 230, 255, 180))

    # 4) 빨간 모자 쓴 작은 눈사람 — 제목 글자 위에 하나 얹음 (PD 2026-11-23)
    def _snowman(cx, by, s):
        """cx 중심, by를 바닥으로 위로 쌓는 눈사람. s=몸통 반지름."""
        cx, by, s = int(cx), int(by), int(s)
        def box(a, b, c, e):
            return [int(a), int(b), int(c), int(e)]
        ol = (150, 180, 210, 255)
        w = max(1, s // 10)
        d.ellipse(box(cx - s, by - 2 * s, cx + s, by), fill=(255, 255, 255, 255), outline=ol, width=w)  # 몸통
        hr = s * 0.62
        hcy = by - 2 * s - hr                                                                          # 머리 중심
        d.ellipse(box(cx - hr, hcy - hr, cx + hr, hcy + hr), fill=(255, 255, 255, 255), outline=ol, width=w)
        er = max(1, int(s * 0.09))
        d.ellipse(box(cx - hr * 0.4 - er, hcy - hr * 0.2 - er, cx - hr * 0.4 + er, hcy - hr * 0.2 + er), fill=(25, 25, 25, 255))
        d.ellipse(box(cx + hr * 0.4 - er, hcy - hr * 0.2 - er, cx + hr * 0.4 + er, hcy - hr * 0.2 + er), fill=(25, 25, 25, 255))
        d.polygon([(cx, int(hcy + hr * 0.05)), (cx, int(hcy + hr * 0.3)), (int(cx + hr * 0.8), int(hcy + hr * 0.15))], fill=(240, 140, 40, 255))  # 당근코
        for bx in (cx - s * 0.2, cx + s * 0.1, cx + s * 0.4):                                          # 몸통 단추
            d.ellipse(box(bx - er * 0.6, by - 2 * s + s * 0.5 - er * 0.6, bx - er * 0.6 + er * 1.2, by - 2 * s + s * 0.5 + er * 0.6), fill=(40, 40, 40, 255))
        by_hat = hcy - hr * 0.78                                                                       # 빨간 비니 모자
        d.rounded_rectangle(box(cx - hr * 0.95, by_hat, cx + hr * 0.95, by_hat + max(3, s * 0.22)), radius=max(2, s // 8), fill=(205, 45, 45, 255))
        d.polygon([(int(cx - hr * 0.78), int(by_hat + 2)), (int(cx + hr * 0.78), int(by_hat + 2)), (int(cx + hr * 0.42), int(by_hat - hr * 1.15)), (int(cx - hr * 0.42), int(by_hat - hr * 1.15))], fill=(215, 55, 55, 255))
        d.ellipse(box(cx - hr * 0.24, by_hat - hr * 1.42, cx + hr * 0.24, by_hat - hr * 0.94), fill=(255, 255, 255, 255))  # 폼폼
    _snowman(x + tw * 0.82, cy - th * 0.08, cap * 2.1)

    # 5) 흩날리는 눈송이 — 아래로 낙하(snow_phase). 송이마다 속도 다르게 + 가로 살짝 흔들림.
    for i in range(70):
        s = (seed >> (i % 48)) & 0xFFFF
        fr = 2 + (s % 7)
        fa = 70 + (s % 150)
        speed = 0.6 + (s % 5) * 0.18
        base_fx = (s * 131 + i * 977) % W
        base_fy = ((s >> 3) * 977 + i * 131) % H
        fy = int(base_fy + snow_phase * H * speed) % H
        fx = int(base_fx + 14 * math.sin(snow_phase * 2 * math.pi + i)) % W   # 좌우 살랑
        d.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=(255, 255, 255, fa))

    def _centered(text, font, yc, fill, sw):
        b = d.textbbox((0, 0), text, font=font, stroke_width=sw)
        w2, h2 = b[2] - b[0], b[3] - b[1]
        xx = (W - w2) / 2 - b[0]
        yy = yc - h2 / 2 - b[1]
        d.text((xx + 3, yy + 4), text, font=font, fill=blue_sh)
        for dxx, dyy in ((-sw, 0), (sw, 0), (0, -sw), (0, sw)):
            d.text((xx + dxx, yy + dyy), text, font=font, fill=(40, 70, 120, 255))
        d.text((xx, yy), text, font=font, fill=fill)

    if kicker:
        _centered(kicker, f_kick, cy - th / 2 - cap - 70, (255, 255, 255, 255), 2)
    if credit:
        _centered(credit, f_cred, cy + th / 2 + 90, ice, 2)
    img.save(out)
    return out


def make_snow_title_anim(title, out_mov, *, kicker="", credit="", W=1080, H=1920,
                         vpos=0.16, title_px=190, dur=5.0, fps=15):
    """눈송이가 아래로 떨어지는 애니메이션 타이틀 = 알파 포함 ProRes 4444 .mov.
    글자·눈모자·눈사람은 고정, 눈송이만 낙하(snow_phase 0→1 루프). make_mv가 PNG 대신 이 영상을
    오버레이해 타이틀 눈이 실제로 내린다. 기본 vpos 상단(펫 얼굴 안 가림)."""
    import subprocess
    import tempfile
    import shutil
    n = max(2, int(dur * fps))
    tmp = Path(tempfile.mkdtemp(prefix="snowtitle_"))
    try:
        for f in range(n):
            make_snow_card(title, tmp / f"f{f:04d}.png", kicker=kicker, credit=credit,
                           W=W, H=H, vpos=vpos, title_px=title_px, snow_phase=f / n)
        subprocess.run(
            ["ffmpeg", "-y", "-framerate", str(fps), "-i", str(tmp / "f%04d.png"),
             "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
             str(out_mov)], check=True, capture_output=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_mov


def make_synthwave_card(title, out, *, kicker="", credit="", W=1080, H=1920, vpos=0.26):
    """★80s synthwave / citypop / outrun 네온 나이트드라이브 EMBLEM (PD: 레퍼런스 비디오 컨셉이 묻어나야 한다).
    레퍼런스(Blinding Lights 류 네온 나이트드라이브)의 미감을 박은 디자인 타이틀 — sunset 그라데이션 chrome
    레터(시안→마젠타→오렌지) + 수평 슬릿(80s 크롬) + 네온 글로우(bloom) + 아웃런 sun + 퍼스펙티브 그리드.
    맨글자 금지 — 디자인된 엠블럼."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(img)
    f_title = _load(_FONTS / "Pretendard-Black.otf", 168)
    f_kick = _load(_FONTS / "Pretendard-ExtraBold.otf", 44)
    f_cred = _load(_FONTS / "Pretendard-SemiBold.otf", 38)
    px = 168
    while px > 90 and d.textlength(title, font=f_title) > W * 0.86:
        px -= 4; f_title = _load(_FONTS / "Pretendard-Black.otf", px)
    cx = W / 2; cy = int(H * vpos)
    tb = d.textbbox((0, 0), title, font=f_title, stroke_width=0)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    tx = cx - tw / 2 - tb[0]; ty = cy - th / 2 - tb[1]

    # outrun SUN disc behind the title (gradient + horizontal slits)
    sun = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sd = ImageDraw.Draw(sun)
    R = int(tw * 0.42); scx, scy = int(cx), int(cy + 10)
    for i in range(R, 0, -1):
        f = i / R
        sd.ellipse([scx - i, scy - i, scx + i, scy + i],
                   fill=(255, int(70 + 150 * (1 - f)), int(150 * f + 40), 255))
    for yy in range(scy, scy + R, 14):
        w = max(0, int((R ** 2 - (yy - scy) ** 2) ** 0.5))
        sd.rectangle([scx - w, yy, scx + w, yy + 6], fill=(0, 0, 0, 0))
    img.alpha_composite(Image.composite(sun, Image.new("RGBA", (W, H), (0, 0, 0, 0)),
                                        sun.split()[3].point(lambda a: int(a * 0.55))))

    # perspective GRID at the bottom (receding neon lines)
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gd = ImageDraw.Draw(grid)
    horizon = int(cy + th * 0.75); vp = cx
    for gx in range(-10, 11):
        gd.line([(vp + gx * 26, horizon), (vp + gx * 150, horizon + 330)], fill=(0, 220, 255, 150), width=2)
    yy, step = horizon, 16
    while yy < horizon + 330:
        gd.line([(0, yy), (W, yy)], fill=(255, 60, 180, 120), width=2); step = int(step * 1.35); yy += step
    img.alpha_composite(grid.filter(ImageFilter.GaussianBlur(1)))

    # TITLE — sunset-gradient chrome letters w/ horizontal slits + neon glow
    y0, y1 = int(ty), int(ty + th)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).text((tx, ty), title, font=f_title, fill=255, stroke_width=3, stroke_fill=255)
    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0)); gp = grad.load()

    def _lerp(a, b, t):
        return tuple(int(a[i] + (b[i] - a[i]) * max(0.0, min(1.0, t))) for i in range(3))
    C_top, C_mid, C_bot = (120, 245, 255), (255, 120, 230), (255, 180, 70)
    for yy in range(max(0, y0), min(H, y1 + 8)):
        t = (yy - y0) / max(1, th)
        col = _lerp(C_top, C_mid, t * 2) if t < 0.5 else _lerp(C_mid, C_bot, (t - 0.5) * 2)
        for xx in range(W):
            gp[xx, yy] = (col[0], col[1], col[2], 255)
    letters = Image.new("RGBA", (W, H), (0, 0, 0, 0)); letters.paste(grad, (0, 0), mask)
    slit = Image.new("L", (W, H), 0); sld = ImageDraw.Draw(slit)
    for yy in range(y0, y0 + int(th * 0.46), 10):
        sld.rectangle([0, yy, W, yy + 3], fill=255)
    letters.putalpha(Image.composite(Image.new("L", (W, H), 0), letters.split()[3], slit))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((tx, ty), title, font=f_title, fill=(255, 60, 200, 230), stroke_width=7, stroke_fill=(255, 60, 200, 230))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(26)))
    glow2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow2).text((tx, ty), title, font=f_title, fill=(0, 220, 255, 210), stroke_width=5, stroke_fill=(0, 220, 255, 210))
    img.alpha_composite(glow2.filter(ImageFilter.GaussianBlur(14)))
    d.text((tx, ty), title, font=f_title, fill=(8, 6, 24, 255), stroke_width=10, stroke_fill=(8, 6, 24, 255))
    d.text((tx, ty), title, font=f_title, fill=(8, 6, 24, 0), stroke_width=6, stroke_fill=(240, 240, 255, 255))
    img.alpha_composite(letters)

    if kicker:
        kb = d.textbbox((0, 0), kicker, font=f_kick); kw2, kh = kb[2] - kb[0], kb[3] - kb[1]
        kx = cx - kw2 / 2 - kb[0]; ky = ty - kh - 56
        kg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(kg).text((kx, ky), kicker, font=f_kick, fill=(0, 220, 255, 230))
        img.alpha_composite(kg.filter(ImageFilter.GaussianBlur(10)))
        d.text((kx, ky), kicker, font=f_kick, fill=(230, 250, 255, 255), stroke_width=2, stroke_fill=(0, 110, 150, 255))
    if credit:
        cb = d.textbbox((0, 0), credit, font=f_cred); cw = cb[2] - cb[0]
        d.text((cx - cw / 2 - cb[0], ty + th + 40), credit, font=f_cred, fill=(255, 190, 120, 255), stroke_width=2, stroke_fill=(40, 16, 8, 255))
    img.save(out); return out


_SYNTHWAVE_KW = ("synthwave", "citypop", "city pop", "outrun", "neon", "night drive", "nightdrive",
                 "midnight", "retrowave", "vaporwave", "시티팝", "신스웨이브", "네온", "아웃런", "레트로웨이브")

_SNOW_KW = ("snow", "christmas", "carol", "winter", "xmas", "snowman", "holiday",
            "캐롤", "크리스마스", "눈", "겨울", "첫눈", "눈사람", "셔플 캐롤")

_MIDCENTURY_KW = ("mid-century", "midcentury", "mid century", "mod", "retro pop", "retro-pop",
                  "checkerboard", "60s", "70s", "미드센츄리", "모드", "레트로")


def auto_card(title, out, *, style="", kicker="", credit="", **kw):
    """★에이전트 진입점: 곡 style 문자열에서 무드를 자동 선택해 카드를 만든다.
    kicker 미지정 시 채널 기본 키커. MV 빌더는 항상 이걸 호출해 타이틀을 자동 생성한다.
    ★타이틀은 스토리보드 1급 요소 — 처음부터 이걸로 설계하라(나중에 붙이는 자막 취급 금지, PD 2026-11-18)."""
    if not kicker:
        kicker = "♪ 랴니레오 오리지널 M/V"
    s = (style or "").lower()
    if any(k in s for k in _SNOW_KW):
        return make_snow_card(title, out, kicker=kicker, credit=credit,
                              **{k: v for k, v in kw.items() if k in ("vpos", "W", "H")})
    if any(k in s for k in _MIDCENTURY_KW):
        return make_midcentury_card(title, out, kicker=kicker, credit=credit,
                                    **{k: v for k, v in kw.items() if k in ("vpos", "W", "H", "subtitle", "title_font")})
    if any(k in s for k in _Y2K_KW):
        return make_y2k_card(title, out, kicker=kicker, credit=credit,
                             **{k: v for k, v in kw.items() if k in ("vpos", "W", "H")})
    if any(k in s for k in _SYNTHWAVE_KW):
        return make_synthwave_card(title, out, kicker=kicker, credit=credit,
                                   **{k: v for k, v in kw.items() if k in ("vpos", "W", "H")})
    mood = auto_mood(style)
    return make_title_card(title, out, kicker=kicker, credit=credit, mood=mood, **kw)


if __name__ == "__main__":
    import sys
    auto_card(sys.argv[1] if len(sys.argv) > 1 else "너는 내 이름도",
              "/tmp/mv_title_demo.png", style="neo-soul R&B ballad",
              kicker="You Don't Know My Name", credit="작곡 라레수 · 작사 함미하비")
    print("saved /tmp/mv_title_demo.png (mood auto-picked)")
