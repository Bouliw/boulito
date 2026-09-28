"""Draw Boulito's visual identity as vector art (AppKit), so that it can be reproduced.

- App icon: an orange ball ("boule"), chubby and smiling, with a beret and a French handlebar mustache,
  listening (waves around it), on an indigo background.
- Menu bar icon: the same ball with beret and mustache as a silhouette ("template" image: black or
  white depending on the theme).

Usage: .venv/bin/python app/icon/draw_icon.py   (then ./app/build.sh to put the icon into Boulito.app)
"""

from pathlib import Path

from AppKit import (NSAffineTransform, NSBezierPath, NSBitmapImageRep, NSColor, NSCompositingOperationClear, NSGradient,
                    NSGraphicsContext, NSMakePoint, NSMakeRect, NSPNGFileType, NSShadow, NSMakeSize)

HERE = Path(__file__).resolve().parent
ICONSET = HERE / "Boulito.iconset"

# Brand colors
INDIGO_TOP = (0.31, 0.27, 0.90)     # #4F46E5
VIOLET_BOTTOM = (0.58, 0.20, 0.92)  # #9333EA
BALL_LIGHT = (1.00, 0.86, 0.45)     # highlight
BALL = (1.00, 0.62, 0.20)           # Boulito orange #FF9E33
BALL_DARK = (0.96, 0.40, 0.12)      # shadow
INK = (0.13, 0.07, 0.30)            # eyes and mouth, very dark indigo
BERET = (0.13, 0.12, 0.22)          # beret, almost black
MUSTACHE = (0.24, 0.13, 0.07)       # mustache, dark brown
MUSTACHE_LIGHT = (0.42, 0.25, 0.14)
BERET_LIGHT = (0.27, 0.25, 0.42)


def rgb(c, alpha=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(c[0], c[1], c[2], alpha)


def canvas(px: int):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
    rep.setSize_(NSMakeSize(px, px))
    context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(context)
    context.setImageInterpolation_(3)  # high quality
    return rep


def save(rep, path: Path) -> None:
    NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(str(path), True)


# Bolder version for the menu bar: at 18 points the loops disappear; clearly upturned
# tips make the mustache recognizable (otherwise it looks like a smile)
BOLD = [
    ((0.03, 0.05), (0.08, 0.08), (0.14, 0.08)),    # notch in the center, then the top of the wing
    ((0.21, 0.08), (0.25, 0.04), (0.29, 0.02)),
    ((0.34, 0.00), (0.38, 0.08), (0.40, 0.19)),    # the tip goes up
    ((0.41, 0.24), (0.37, 0.26), (0.35, 0.21)),    # rounded end
    ((0.33, 0.13), (0.31, 0.08), (0.26, 0.07)),    # back under the tip
    ((0.20, -0.10), (0.08, -0.12), (0.0, -0.06)),  # bottom of the wing, thick
]


def mustache(cx: float, cy: float, r: float, bold: bool = False):
    """French handlebar mustache: two thick wings in the center, thin tips curled up into loops."""
    right = BOLD if bold else [  # right half, in units of r (y up), from the top of the center
        ((0.06, 0.07), (0.16, 0.08), (0.24, 0.03)),    # top edge
        ((0.29, 0.00), (0.34, 0.02), (0.36, 0.09)),    # toward the tip
        ((0.37, 0.14), (0.33, 0.17), (0.29, 0.15)),    # loop
        ((0.32, 0.13), (0.33, 0.08), (0.29, 0.065)),   # back from the loop
        ((0.23, 0.05), (0.20, -0.06), (0.10, -0.07)),  # bottom edge
        ((0.05, -0.075), (0.02, -0.05), (0.0, -0.035)),
    ]
    path = NSBezierPath.bezierPath()
    for side in (1, -1):
        path.moveToPoint_(NSMakePoint(cx, cy))
        for c1, c2, end in right:
            pt = lambda p: NSMakePoint(cx + side * p[0] * r, cy + p[1] * r)
            path.curveToPoint_controlPoint1_controlPoint2_(pt(end), pt(c1), pt(c2))
        path.closePath()
    return path


def face(s: float, cx: float, cy: float, r: float, ink, clear: bool = False) -> None:
    """Eyes, big smile and mustache. clear: cut out of the ball (menu bar template icon),
    with a bolder mustache and a small round mouth: at 18 points, the big smile would blend into it."""
    context = NSGraphicsContext.currentContext()
    if clear:
        context.setCompositingOperation_(NSCompositingOperationClear)
    ink.set()
    for dx in (-0.28, 0.28):  # eyes
        w, h = 0.17 * r, 0.26 * r
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx + dx * r - w / 2, cy + 0.06 * r, w, h)).fill()
    if clear:  # menu bar: bolder mustache, and a small mouth below so it can be recognized
        mustache(cx, cy - 0.12 * r, 1.7 * r, bold=True).fill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 0.13 * r, cy - 0.62 * r, 0.26 * r, 0.22 * r)).fill()
        context.setCompositingOperation_(2)  # NSCompositingOperationSourceOver
        return
    # Smile: half-oval, flat edge on top
    top, half_w, depth = cy - 0.16 * r, 0.30 * r, 0.28 * r
    NSGraphicsContext.saveGraphicsState()
    NSBezierPath.bezierPathWithRect_(NSMakeRect(cx - half_w - 1, top - depth - 1, 2 * half_w + 2, depth + 1)).addClip()
    mouth = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - half_w, top - depth, 2 * half_w, 2 * depth))
    mouth.fill()
    if not clear:  # tongue
        mouth.addClip()
        NSColor.colorWithSRGBRed_green_blue_alpha_(1.0, 0.42, 0.50, 1).set()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - 0.16 * r, top - depth - 0.06 * r, 0.32 * r, 0.21 * r)).fill()
    NSGraphicsContext.restoreGraphicsState()
    # Mustache, resting on the top of the smile, with a highlight
    NSGraphicsContext.saveGraphicsState()
    shadow = NSShadow.alloc().init()
    shadow.setShadowOffset_(NSMakeSize(0, -3 * s))
    shadow.setShadowBlurRadius_(6 * s)
    shadow.setShadowColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0.35, 0.12, 0.02, 0.35))
    shadow.set()
    rgb(MUSTACHE).set()
    shape = mustache(cx, cy - 0.10 * r, 1.25 * r)
    shape.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient.alloc().initWithStartingColor_endingColor_(rgb(MUSTACHE_LIGHT), rgb(MUSTACHE)).drawInBezierPath_angle_(shape, -90)


def oval(cx: float, cy: float, w: float, h: float, degrees: float = 0.0):
    """Oval centered at (cx, cy), rotated by degrees."""
    path = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(-w / 2, -h / 2, w, h))
    t = NSAffineTransform.transform()
    t.translateXBy_yBy_(cx, cy)
    t.rotateByDegrees_(degrees)
    path.transformUsingAffineTransform_(t)
    return path


def beret(cx: float, top: float, r: float):
    """Beret tilted to the right, sitting on top of the head: (cap, stem)."""
    cap = oval(cx + 0.12 * r, top + 0.02 * r, 1.30 * r, 0.46 * r, -9)
    stem = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx + 0.14 * r, top + 0.20 * r, 0.09 * r, 0.16 * r), 0.045 * r, 0.045 * r)
    return cap, stem


def app_icon(px: int) -> object:
    rep = canvas(px)
    s = px / 1024
    # Background: macOS rounded square, with a soft shadow
    shadow = NSShadow.alloc().init()
    shadow.setShadowOffset_(NSMakeSize(0, -12 * s))
    shadow.setShadowBlurRadius_(22 * s)
    shadow.setShadowColor_(NSColor.colorWithWhite_alpha_(0, 0.28))
    NSGraphicsContext.saveGraphicsState()
    shadow.set()
    square = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(100 * s, 100 * s, 824 * s, 824 * s), 185 * s, 185 * s)
    rgb(VIOLET_BOTTOM).set()
    square.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient.alloc().initWithStartingColor_endingColor_(rgb(INDIGO_TOP), rgb(VIOLET_BOTTOM)).drawInBezierPath_angle_(square, -60)
    # Waves around the ball: Boulito is listening
    rgb((1, 1, 1), 0.16).set()
    for radius, width in ((330, 16), (392, 12)):
        ring = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect((512 - radius) * s, (470 - radius) * s, 2 * radius * s, 2 * radius * s))
        ring.setLineWidth_(width * s)
        ring.stroke()
    # The ball, chubby (wider than tall): orange radial gradient, highlight at the top left
    cx, cy, r = 512 * s, 455 * s, 250 * s
    rx = 1.13 * r
    ball = oval(cx, cy, 2 * rx, 2 * r)
    NSGraphicsContext.saveGraphicsState()
    shadow.setShadowOffset_(NSMakeSize(0, -10 * s))
    shadow.setShadowBlurRadius_(24 * s)
    shadow.setShadowColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0.12, 0.05, 0.35, 0.45))
    shadow.set()
    rgb(BALL).set()
    ball.fill()
    NSGraphicsContext.restoreGraphicsState()
    gradient = NSGradient.alloc().initWithColors_([rgb(BALL_LIGHT), rgb(BALL), rgb(BALL_DARK)])
    gradient.drawInBezierPath_relativeCenterPosition_(ball, NSMakePoint(-0.35, 0.40))
    # Big pink cheeks
    rgb((1.0, 0.36, 0.45), 0.42).set()
    for dx in (-0.62, 0.62):
        oval(cx + dx * rx, cy - 0.02 * r, 0.40 * r, 0.24 * r).fill()
    face(s, cx, cy, r, rgb(INK))
    # Highlights in the eyes
    rgb((1, 1, 1), 0.9).set()
    for dx in (-0.28, 0.28):
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx + dx * r - 0.01 * r, cy + 0.22 * r, 0.055 * r, 0.055 * r)).fill()
    # Beret, with its shadow on the forehead and a slight highlight
    cap, stem = beret(cx, cy + 0.86 * r, r)
    NSGraphicsContext.saveGraphicsState()
    shadow.setShadowOffset_(NSMakeSize(0, -6 * s))
    shadow.setShadowBlurRadius_(14 * s)
    shadow.setShadowColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(0.35, 0.12, 0.02, 0.45))
    shadow.set()
    rgb(BERET).set()
    cap.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient.alloc().initWithStartingColor_endingColor_(rgb(BERET_LIGHT), rgb(BERET)).drawInBezierPath_relativeCenterPosition_(
        cap, NSMakePoint(-0.3, 0.5))
    rgb(BERET).set()
    stem.fill()
    return rep


def menubar_icon(px: int) -> object:
    """Menu bar silhouette: solid chubby ball, beret separated by a thin gap, eyes and mouth cut out."""
    rep = canvas(px)
    s = px / 18
    cx, cy, r = 9 * s, 7.4 * s, 6.3 * s
    rx = 1.13 * r
    context = NSGraphicsContext.currentContext()
    NSColor.blackColor().set()
    oval(cx, cy, 2 * rx, 2 * r).fill()
    face(s, cx, cy, r, NSColor.blackColor(), clear=True)
    cap, stem = beret(cx, cy + 0.86 * r, r)
    context.setCompositingOperation_(NSCompositingOperationClear)  # thin gap between the beret and the head
    gap = NSBezierPath.bezierPathWithCGPath_(cap.CGPath()) if hasattr(cap, "CGPath") else cap.copy()
    gap.setLineWidth_(1.6 * s / 2)
    gap.stroke()
    context.setCompositingOperation_(2)
    NSColor.blackColor().set()
    cap.fill()
    stem.fill()
    return rep


def main() -> None:
    ICONSET.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            save(app_icon(size * scale), ICONSET / name)
    save(menubar_icon(18), HERE / "menubar.png")
    save(menubar_icon(36), HERE / "menubar@2x.png")
    print("Icons written to", HERE)


if __name__ == "__main__":
    main()
