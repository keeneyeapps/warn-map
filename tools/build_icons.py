"""Build the WARN Map app icons from Luna Montgomery's moose-head master.

The moose-head artwork is © Luna Montgomery, all rights reserved, used by permission (see LICENSE). The master is
not in this repository; pass its path (the Android repo's art/moose_head_master.png):

    python tools/build_icons.py --moose ../WARN/art/moose_head_master.png

Each icon is a dark square with a few pale roads and one river drawn here (no real place), the moose as a yellow
silhouette centred on top, and a thin yellow/black hazard-stripe border. Maskable icons keep the whole moose inside
the safe zone (a centred circle of 40% of the size).
"""
import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SS = 4  # supersampling

BACKGROUND = (23, 27, 29)
ROAD = (74, 84, 86)
ROAD_MINOR = (52, 60, 62)
RIVER = (38, 70, 92)
STRIPE_YELLOW = (253, 216, 53)
STRIPE_BLACK = (16, 19, 21)
MOOSE = (255, 210, 0)  # WARN brand yellow, as in the Android launcher icon
OUTLINE = (16, 19, 21)

MASKABLE_SAFE_RADIUS = 0.40  # of the icon size


def moose_alpha(master_path):
    master = Image.open(master_path).convert("RGBA")
    master = master.crop(master.getbbox())
    return master.getchannel("A")


def stripe_border(size, width, period):
    """A frame `width` px wide of 45-degree yellow/black stripes, transparent inside."""
    stripes = Image.new("RGBA", (size, size), STRIPE_BLACK + (255,))
    draw = ImageDraw.Draw(stripes)
    half = period / 2
    k = -size
    while k < 2 * size:
        draw.polygon([(k, 0), (k + half, 0), (k + half - size, size), (k - size, size)], fill=STRIPE_YELLOW + (255,))
        k += period
    frame = Image.new("L", (size, size), 255)
    ImageDraw.Draw(frame).rectangle([width, width, size - 1 - width, size - 1 - width], fill=0)
    stripes.putalpha(frame)
    return stripes


def road_pattern(size):
    """A made-up road map in unit coordinates: a few roads, one river. Nothing recognisable."""
    im = Image.new("RGBA", (size, size), BACKGROUND + (255,))
    draw = ImageDraw.Draw(im)

    def line(points, colour, width):
        draw.line([(x * size, y * size) for x, y in points], fill=colour + (255,), width=max(1, round(width * size)),
                  joint="curve")

    river = [(-0.05, 0.30), (0.12, 0.34), (0.25, 0.45), (0.33, 0.62), (0.50, 0.70), (0.66, 0.68), (0.80, 0.80),
             (0.92, 0.95), (1.05, 1.02)]
    line(river, RIVER, 0.035)
    for points in ([(0.0, 0.18), (0.40, 0.22), (0.70, 0.12), (1.0, 0.16)],
                   [(0.10, 1.0), (0.18, 0.62), (0.42, 0.40), (0.62, 0.30), (1.0, 0.36)],
                   [(0.70, 0.0), (0.66, 0.30), (0.74, 0.60), (0.70, 1.0)]):
        line(points, ROAD, 0.022)
    for points in ([(0.0, 0.84), (0.30, 0.82), (0.46, 0.90), (0.60, 1.0)],
                   [(0.42, 0.40), (0.38, 0.0)],
                   [(0.74, 0.60), (1.0, 0.56)],
                   [(0.18, 0.62), (0.0, 0.55)]):
        line(points, ROAD_MINOR, 0.014)
    return im


def moose_layer(alpha, size, width_fraction, centre):
    width = round(size * width_fraction)
    height = round(width * alpha.height / alpha.width)
    mask = alpha.resize((width, height), Image.LANCZOS)
    outline = mask.filter(ImageFilter.MaxFilter(2 * max(1, round(size * 0.006)) + 1))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # The head sits low in its box (wide antlers, narrow muzzle); nudge it down slightly to look centred.
    x = round(centre[0] - width / 2)
    y = round(centre[1] - height / 2 + size * 0.015)
    layer.paste(Image.new("RGBA", outline.size, OUTLINE + (255,)), (x - (outline.width - width) // 2, y), outline)
    layer.paste(Image.new("RGBA", mask.size, MOOSE + (255,)), (x, y), mask)
    return layer


def max_safe_width(alpha, safe_radius):
    """Largest moose width (fraction of the icon) whose opaque pixels, outline included, stay inside a centred
    circle of safe_radius, with the same placement as moose_layer."""
    probe = 1000
    best = 0.0
    for tenth in range(300, 900, 5):
        fraction = tenth / 1000
        layer = moose_layer(alpha, probe, fraction, (probe / 2, probe / 2))
        a = layer.getchannel("A").point(lambda v: 255 if v > 16 else 0)
        bbox = a.getbbox()
        inside = True
        # Check the opaque pixels nearest the corners of their bounding box and the extremes.
        pixels = a.load()
        for y in range(bbox[1], bbox[3], 2):
            for x in range(bbox[0], bbox[2], 2):
                if pixels[x, y] and math.hypot(x - probe / 2, y - probe / 2) > safe_radius * probe:
                    inside = False
                    break
            if not inside:
                break
        if not inside:
            break
        best = fraction
    return best


def icon(alpha, size, moose_width):
    big = size * SS
    im = road_pattern(big)
    # On maskable icons, launcher masks may crop the border; the moose stays whole inside the safe zone.
    im.alpha_composite(stripe_border(big, round(big * 0.045), big * 0.09))
    im.alpha_composite(moose_layer(alpha, big, moose_width, (big / 2, big / 2)))
    return im.resize((size, size), Image.LANCZOS).convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Build the WARN Map icons from the moose-head master")
    parser.add_argument("--moose", type=Path, required=True, help="Luna Montgomery's moose_head_master.png")
    args = parser.parse_args()
    alpha = moose_alpha(args.moose)
    out = ROOT / "icons"
    safe_width = max_safe_width(alpha, MASKABLE_SAFE_RADIUS - 0.01)
    print(f"Maskable moose width: {safe_width:.3f} of the icon (any-purpose icons: 0.700)")
    for name, size, maskable in (("icon-192.png", 192, False), ("icon-512.png", 512, False),
                                 ("icon-192-maskable.png", 192, True), ("icon-512-maskable.png", 512, True),
                                 ("apple-touch-icon.png", 180, False)):
        icon(alpha, size, safe_width if maskable else 0.70).save(out / name, optimize=True)
        print(f"{name}: {size}x{size}")


if __name__ == "__main__":
    main()
