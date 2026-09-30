"""Build Tantalus's icon from a sibling family icon.

    python scripts/make_icon.py --src <sibling>/app-icon.png [--preview out.png]

The family icons share one dragon (a rounded dark square, a gradient dragon, a cream-gold glyph of about 400 px
centred at (627, 768)). The recipe keeps the dragon's own shading instead of rebuilding it from luminance, so the
result stays sharp:

1. find the dragon by colour (saturated, not cream) and the sibling's glyph (cream-gold pixels plus their dark
   outline inside the standard glyph box);
2. turn the dragon's hue to Tantalus's pomegranate-orange, keeping saturation and value per pixel (gradient and
   anti-aliasing survive); the eye keeps a light colour;
3. clear the old glyph (only the glyph box is touched) and draw the new one on top, larger than the old footprint,
   so no repaired pixel stays visible: a shopping bag with a fruit cut out of it (restocks, and the fruit that
   Tantalus never reaches), filled with the family's gold gradient and a dark outline.

Outputs: app-icon.png (1254²), client/public/icon-512.png, icon-192.png, favicon.ico (16-256) and
dist-icons/Tantalus hoard.png (for the shared Icons folder). Needs: pillow, numpy, opencv-python-headless.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1254
TARGET_HUE = 10           # OpenCV hue units (0-180): a warm pomegranate orange
SAT_SCALE = 1.05
VALUE_SCALE = 1.12
EYE_RGB = (255, 236, 196)
GOLD_TOP = (0xF8, 0xD8, 0x8C)
GOLD_BOTTOM = (0xE0, 0xA2, 0x42)
OUTLINE = (6, 10, 24)
GLYPH_BOX = (412, 568, 842, 972)   # the standard glyph area of the family icons, with a small margin


def default_source() -> Path | None:
    for name in ("Babels hoard.png", "Hoard link.png"):
        for folder in (ROOT.parent / "Icons", ROOT.parent / "icons"):
            if (folder / name).is_file():
                return folder / name
    return None


def masks(bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    x0, y0, x1, y1 = GLYPH_BOX
    box = np.zeros(h.shape, bool)
    box[y0:y1, x0:x1] = True
    cream = (h >= 8) & (h <= 35) & (s > 40) & (v > 120) & box
    # the glyph's dark outline: dilate the cream shape and keep what is inside the box
    glyph = cv2.dilate(cream.astype(np.uint8) * 255, np.ones((29, 29), np.uint8)) > 0
    glyph &= box
    # purple dragon, including its anti-aliased edge against the navy background (lower saturation, same hue family)
    dragon = (((h >= 122) & (h <= 179)) | (h <= 6)) & (s > 18) & (v > 14) & ~cream
    eye = dragon & (v > 215) & (s > 150)
    return dragon, glyph, eye


def recolour(bgr: np.ndarray, dragon: np.ndarray, eye: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
    # hard mask, no blending: interpolating hue between purple and orange would pass through green on the edges
    new = hsv.copy()
    new[..., 0] = TARGET_HUE
    new[..., 1] = np.clip(hsv[..., 1] * SAT_SCALE, 0, 255)
    new[..., 2] = np.clip(hsv[..., 2] * VALUE_SCALE, 0, 255)
    out = np.where(dragon[..., None], new, hsv)
    rgb = cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_HSV2RGB).astype(np.float32)
    e = cv2.GaussianBlur(eye.astype(np.float32), (5, 5), 0)[..., None]
    rgb = rgb * (1 - e) + np.array(EYE_RGB, np.float32) * e
    return np.clip(rgb, 0, 255).astype(np.uint8)


def clear_glyph(rgb: np.ndarray, glyph: np.ndarray) -> np.ndarray:
    """Repair the old glyph area (it ends up under the new, larger glyph anyway)."""
    mask = cv2.dilate(glyph.astype(np.uint8) * 255, np.ones((5, 5), np.uint8))
    return cv2.inpaint(rgb, mask, 9, cv2.INPAINT_TELEA)


def glyph_layer(scale: int = 4) -> Image.Image:
    """RGBA 1254² layer with the bag, drawn at `scale`× and downsampled."""
    big = SIZE * scale

    def P(x: float, y: float) -> tuple[float, float]:
        return (x * scale, y * scale)

    x0, y0, x1, y1 = GLYPH_BOX
    # bag body: slightly wider at the bottom, rounded bottom corners, covering the old glyph footprint
    top, bottom = 665, 988
    body = [P(452, top), P(802, top), P(846, bottom), P(408, bottom)]
    handle = (P(532, 560), P(722, 770))

    def shape(draw: ImageDraw.ImageDraw, grow: int, fill) -> None:
        g = grow * scale
        pts = [(body[0][0] - g, body[0][1] - g), (body[1][0] + g, body[1][1] - g), (body[2][0] + g, body[2][1] + g),
               (body[3][0] - g, body[3][1] + g)]
        draw.polygon(pts, fill=fill)
        w = (26 + 2 * grow) * scale
        draw.arc([handle[0][0] - g, handle[0][1] - g, handle[1][0] + g, handle[1][1] + g], start=180, end=360, fill=fill, width=w)

    def fruit(draw: ImageDraw.ImageDraw, fill) -> None:
        cx, cy = 627, 845
        # apple: two overlapping lobes and a notch at the top, a stem and a leaf
        draw.ellipse([P(cx - 92, cy - 70), P(cx + 6, cy + 88)], fill=fill)
        draw.ellipse([P(cx - 6, cy - 70), P(cx + 92, cy + 88)], fill=fill)
        draw.ellipse([P(cx - 60, cy - 20), P(cx + 60, cy + 100)], fill=fill)
        draw.line([P(cx, cy - 64), P(cx + 10, cy - 112)], fill=fill, width=16 * scale)
        draw.polygon([P(cx + 16, cy - 96), P(cx + 58, cy - 128), P(cx + 92, cy - 112), P(cx + 50, cy - 82)], fill=fill)

    outline = Image.new("L", (big, big), 0)
    shape(ImageDraw.Draw(outline), 12, 255)
    gold_mask = Image.new("L", (big, big), 0)
    shape(ImageDraw.Draw(gold_mask), 0, 255)
    fruit(ImageDraw.Draw(gold_mask), 0)       # cut the fruit out of the bag

    t, b = np.array(GOLD_TOP, np.float32), np.array(GOLD_BOTTOM, np.float32)
    ramp = np.linspace(0, 1, big, dtype=np.float32)[:, None, None]
    gold = Image.fromarray(np.broadcast_to(t * (1 - ramp) + b * ramp, (big, big, 3)).astype(np.uint8), "RGB")
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    layer.paste(Image.new("RGB", (big, big), OUTLINE), (0, 0), outline)
    layer.paste(gold, (0, 0), gold_mask)
    return layer.resize((SIZE, SIZE), Image.LANCZOS)


def compose(src: Image.Image) -> Image.Image:
    bgr = cv2.cvtColor(np.array(src.convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)), cv2.COLOR_RGB2BGR)
    dragon, glyph, eye = masks(bgr)
    rgb = recolour(bgr, dragon & ~glyph, eye & ~glyph)
    rgb = clear_glyph(rgb, glyph)
    base = Image.fromarray(rgb, "RGB").convert("RGBA")
    base.alpha_composite(glyph_layer())
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--src", type=Path, default=None, help="A sibling family icon (1254², gold glyph at the standard place)")
    parser.add_argument("--preview", type=Path, default=None, help="Also write a 400 px preview here")
    args = parser.parse_args()
    src_path = args.src or default_source()
    if src_path is None or not src_path.is_file():
        print("no sibling icon found: pass --src")
        return 2
    icon = compose(Image.open(src_path))
    icon.save(ROOT / "app-icon.png", optimize=True)
    public = ROOT / "client" / "public"
    public.mkdir(parents=True, exist_ok=True)
    icon.resize((512, 512), Image.LANCZOS).save(public / "icon-512.png", optimize=True)
    small = icon.resize((192, 192), Image.LANCZOS).filter(ImageFilter.UnsharpMask(radius=1, percent=60, threshold=2))
    small.save(public / "icon-192.png", optimize=True)
    icon.convert("RGBA").save(public / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    dist = ROOT / "dist-icons"
    dist.mkdir(exist_ok=True)
    icon.save(dist / "Tantalus hoard.png", optimize=True)
    if args.preview:
        icon.resize((400, 400), Image.LANCZOS).convert("RGB").save(args.preview)
    print(f"icon written from {src_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
