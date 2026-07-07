"""Generate a Claude notification icon and convert it to the watch's pixel formats.

Pixel-format conversions are byte-exact ports of Gadgetbridge's XiaomiBitmapUtils
(they replicate its bit math from a packed 0xAARRGGBB int, quirks and all, so the
watch decodes our bytes the same way it decodes GB's).
"""
import math
import struct

from PIL import Image, ImageDraw

# Gadgetbridge pixel-format codes (watch tells us which one it wants)
PF_RGB565_LE = 0
PF_RGB565_BE = 1
PF_XRGB8888_LE = 2
PF_ARGB8888_LE = 3
PF_ARGB8565_LE = 7
PF_ABGR8565_LE = 8

CORAL = (204, 120, 92, 255)   # Claude warm coral
WHITE = (255, 255, 255, 255)


def claude_icon(size: int) -> Image.Image:
    """A coral tile with a white radial sunburst — the Claude mark, at `size`px."""
    s = max(size, 8)
    ss = s * 4  # supersample for smooth edges
    img = Image.new("RGBA", (ss, ss), CORAL)
    d = ImageDraw.Draw(img)
    cx = cy = ss / 2
    rays = 12
    inner = ss * 0.10
    outer = ss * 0.40
    w = max(2, int(ss * 0.05))
    for i in range(rays):
        a = (i / rays) * 2 * math.pi
        x0 = cx + inner * math.cos(a)
        y0 = cy + inner * math.sin(a)
        x1 = cx + outer * math.cos(a)
        y1 = cy + outer * math.sin(a)
        d.line([(x0, y0), (x1, y1)], fill=WHITE, width=w)
    d.ellipse([cx - inner, cy - inner, cx + inner, cy + inner], fill=WHITE)
    return img.resize((s, s), Image.LANCZOS)


def _pixels_argb_int(img: Image.Image):
    """Yield each pixel as Android-style packed int 0xAARRGGBB, row-major."""
    im = img.convert("RGBA")
    for y in range(im.height):
        for x in range(im.width):
            r, g, b, a = im.getpixel((x, y))
            yield (a << 24) | (r << 16) | (g << 8) | b


def convert(pixel_format: int, img: Image.Image, size: int) -> bytes:
    """Return raw bitmap bytes in the requested pixel format (port of
    XiaomiBitmapUtils.convertToPixelFormat)."""
    im = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    px = list(_pixels_argb_int(im))
    out = bytearray()

    if pixel_format in (PF_RGB565_LE, PF_RGB565_BE):
        # GB converts both LE and BE using little-endian putShort (their BE path
        # calls convertToRgb565(..., true)), so we match that.
        for p in px:
            r = (p >> 19) & 0x1F
            g = (p >> 10) & 0x3F
            b = p & 0x1F
            out += struct.pack("<H", ((r << 11) | (g << 5) | b) & 0xFFFF)
    elif pixel_format in (PF_XRGB8888_LE, PF_ARGB8888_LE):
        for p in px:
            out += struct.pack("<I", p & 0xFFFFFFFF)      # LE int == B,G,R,A
    elif pixel_format in (PF_ARGB8565_LE, PF_ABGR8565_LE):
        swap = pixel_format == PF_ABGR8565_LE
        for p in px:
            a = (p >> 24) & 0xFF
            r = (p >> 19) & 0x1F
            g = (p >> 10) & 0x3F
            b = (p >> 3) & 0x1F
            hi, lo = (b, r) if swap else (r, b)
            out += struct.pack("<H", ((hi << 11) | (g << 5) | lo) & 0xFFFF)
            out += bytes([a])
    else:
        return b""
    return bytes(out)


if __name__ == "__main__":  # quick offline check
    for sz in (32, 48):
        ic = claude_icon(sz)
        for pf, bpp in ((PF_RGB565_LE, 2), (PF_ARGB8888_LE, 4), (PF_ARGB8565_LE, 3)):
            data = convert(pf, ic, sz)
            assert len(data) == sz * sz * bpp, (pf, len(data))
        ic.save("claude_icon_%d.png" % sz)
    print("watchicon OK (formats + sizes match)")
