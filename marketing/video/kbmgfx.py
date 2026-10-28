"""KBM graphic helpers: render the supplied KBM logo (unaltered SVG) and compose KBM-style
frames/banners with Plus Jakarta Sans. Used for docs banners, thumbnails and video frames."""

from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = Path(__file__).resolve().parents[2]
BRAND = REPO / "docs" / "assets" / "brand" / "kbm"
#: Plus Jakarta Sans (SIL OFL) converted from the frontend's @fontsource package by ensure_fonts()
FONTS = Path(__file__).resolve().parent / ".fonts"
BLUE = (0x1A, 0x47, 0x96)
NAVY = (0x09, 0x14, 0x50)
DEEP = (0x1F, 0x38, 0x64)
TEXT = (0x2D, 0x35, 0x48)
SLATE = (0x5A, 0x64, 0x78)
TINT = (0xED, 0xF3, 0xFC)
LINE = (0xCC, 0xD8, 0xED)
WHITE = (255, 255, 255)


def ensure_fonts() -> None:
    """Convert the WOFF files of @fontsource/plus-jakarta-sans (frontend/node_modules) to TTF."""
    from fontTools.ttLib import TTFont

    FONTS.mkdir(exist_ok=True)
    pkg = REPO / "frontend" / "node_modules" / "@fontsource" / "plus-jakarta-sans" / "files"
    for w in (400, 500, 600, 700, 800):
        out = FONTS / f"PJS-{w}.ttf"
        if not out.exists():
            t = TTFont(str(pkg / f"plus-jakarta-sans-latin-{w}-normal.woff"))
            t.flavor = None
            t.save(str(out))


def font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / f"PJS-{weight}.ttf"), size)


def logo(name: str, width: int) -> Image.Image:
    """kbm-logo, kbm-logo-white, kbm-mark, kbm-mark-white at `width` px (aspect kept)."""
    png = cairosvg.svg2png(url=f"{BRAND}/{name}.svg", output_width=width)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def lockup(height: int, white: bool = False, subtitle: str | None = None) -> Image.Image:
    """[KBM mark] | DBManager, `height` = mark height."""
    mark = logo("kbm-mark-white" if white else "kbm-mark", int(height * 491 / 163))
    fs = int(height * 0.95)
    f = font(fs, 700)
    tw = int(f.getlength("DBManager"))
    gap = int(height * 0.42)
    sub_f = font(max(10, int(fs * 0.36)), 600) if subtitle else None
    w = mark.width + gap * 2 + 2 + max(tw, int(sub_f.getlength(subtitle.upper())) + 4 if sub_f else 0)
    h = int(height * 1.3)
    im = Image.new("RGBA", (w + 4, h), (0, 0, 0, 0))
    im.alpha_composite(mark, (0, (h - mark.height) // 2))
    d = ImageDraw.Draw(im)
    x = mark.width + gap
    d.line(
        [(x, int(h * 0.08)), (x, int(h * 0.92))],
        fill=(255, 255, 255, 110) if white else LINE + (255,),
        width=max(1, height // 30),
    )
    tx = x + gap
    col = WHITE if white else BLUE
    if subtitle:
        d.text((tx, int(h * 0.02)), "DBManager", font=f, fill=col)
        d.text((tx + 2, int(h * 0.72)), subtitle.upper(), font=sub_f, fill=(255, 255, 255, 190) if white else SLATE)
    else:
        d.text((tx, (h - fs) // 2 - int(fs * 0.12)), "DBManager", font=f, fill=col)
    return im


def motif(im: Image.Image, strength: float = 1.0, bands=(0.31, 0.44), dots=(0.05, 0.56)) -> None:
    """KBM cover motif on a blue image: horizontal bands, dot grid, ringed circles, thin lines."""
    W, H = im.size
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    a = lambda v: int(v * 255 * strength)  # noqa: E731
    d.rectangle([0, int(H * bands[0]), W, int(H * bands[0]) + max(4, H // 90)], fill=(255, 255, 255, a(0.06)))
    d.rectangle([0, int(H * bands[1]), W, int(H * bands[1]) + max(3, H // 130)], fill=(255, 255, 255, a(0.06)))
    r = max(3, H // 150)
    for i in range(11):
        cx = int(W * dots[0]) + (i % 4) * int(H * 0.05)
        cy = int(H * dots[1]) + (i // 4) * int(H * 0.034)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, a(0.2)))
    for cx, cy, rr, inner in [(0.84, 0.22, 0.022, 0.012), (0.91, 0.28, 0.04, 0.024)]:
        X, Y, R, Ri = int(W * cx), int(H * cy), int(H * rr * 1.6), int(H * inner * 1.6)
        d.ellipse([X - R, Y - R, X + R, Y + R], outline=(255, 255, 255, a(0.3)), width=max(1, H // 500))
        d.ellipse([X - Ri, Y - Ri, X + Ri, Y + Ri], fill=(255, 255, 255, a(0.12)))
    for x, y0, y1 in [(0.78, 0.08, 0.3), (0.84, 0.06, 0.19), (0.91, 0.05, 0.23)]:
        d.line(
            [(int(W * x), int(H * y0)), (int(W * x), int(H * y1))], fill=(255, 255, 255, a(0.2)), width=max(1, H // 600)
        )
    im.alpha_composite(ov)


def blue_canvas(w: int, h: int, navy: bool = False, **kw) -> Image.Image:
    im = Image.new("RGBA", (w, h), (NAVY if navy else BLUE) + (255,))
    motif(im, **kw)
    return im


def tracked(d: ImageDraw.ImageDraw, xy, text: str, f, fill, spacing: float = 0.18) -> int:
    """Upper-case label with KBM's wide letter spacing; returns the end x."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + f.size * spacing
    return int(x)


def shadowed(card: Image.Image, radius: int = 18, blur: int = 24, offset: int = 10) -> Image.Image:
    """Card with rounded corners and a soft navy shadow (KBM slide cards)."""
    w, h = card.size
    pad = blur * 2
    out = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad, pad + offset, pad + w, pad + h + offset], radius, fill=NAVY + (110,))
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius, fill=255)
    rc = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rc.paste(card, (0, 0), mask)
    out.alpha_composite(rc, (pad, pad))
    return out
