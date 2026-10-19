import json, math, os, sys
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import REPO  # noqa: E402
MOOSE_YELLOW = (255, 210, 0)
def icon_checks():
    out, ok = {}, True
    expected = {"icon-192.png": 192, "icon-512.png": 512, "icon-192-maskable.png": 192, "icon-512-maskable.png": 512,
                "apple-touch-icon.png": 180}
    for name, size in expected.items():
        im = Image.open(os.path.join(REPO, "icons", name)).convert("RGB")
        moose = [(x, y) for y in range(im.height) for x in range(im.width)
                 if all(abs(a - b) <= 40 for a, b in zip(im.getpixel((x, y)), MOOSE_YELLOW))]
        xs, ys = [p[0] for p in moose], [p[1] for p in moose]
        width_fraction = (max(xs) - min(xs) + 1) / size
        reach = max(math.hypot(x + 0.5 - size / 2, y + 0.5 - size / 2) for x, y in moose) / size
        out[name] = {"size": im.size, "moose_width": round(width_fraction, 3), "moose_reach": round(reach, 3)}
        ok &= im.size == (size, size)
        if "maskable" in name:
            ok &= reach <= 0.40  # the safe zone: a centred circle of radius 40% of the icon
    return out, ok



if __name__ == "__main__":
    out, ok = icon_checks()
    print(json.dumps(out))
    sys.exit(0 if ok else 1)
