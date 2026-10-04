"""Build LandingAgent in the ACTIVE, dedicated Rive file; python3 build_landing.py <colour>.

Coral: investigation/conversation. Black: build/repair/done. Mint: present/nod.
All colours expose the same pose enum. Editor calls use the reference builder's
Rive 0.6 shapes through mcp2; the HTTP session must already be initialized.
"""
from __future__ import annotations

import argparse
import base64
import json
import xml.etree.ElementTree as ET

from flatten import flatten
from landing_common import (
    ARC, COLOURS, EASE, EYE, GEN, IN, INK, OP, OUT, R, SX, SY,
    VX, VY, WHITE, X, Y, Animation, Command, Keyframe, Point, Record, api,
    body, children, converters, export_rig, fill, frame, group, has_type, number, oid, one,
    parametric, record, records, round_stroke, rows, save_binding, set_props,
    shapes, string, stroke, view_model, write_animations, new_artboard,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("colour", choices=("coral", "black", "mint"))
NAME: str = parser.parse_args().colour
BODY, SHAPE, EY, TOP = COLOURS[NAME]
EYE_INK = WHITE if NAME == "black" else INK
POSES = ("idle", "investigate", "realise", "listen", "speak", "nod", "concern",
         "build", "repair", "done", "present")
CACHE = GEN / f".landing-{NAME}-bindings.json"
SVGDIR = GEN.parent / "source/vectors"
TS, TE, TO = 114, 115, 116
STREAK = 30


def accessory_parts(state: str) -> list[str]:
    path = SVGDIR / f"acc-{NAME}-{state}.svg"
    source = ET.fromstring(path.read_text())
    groups = [e for e in source if e.get("transform") == "translate(22 40) scale(0.78)"]
    if len(groups) != 1 or len(groups[0]) <= 3:
        raise ValueError(f"{path}: expected the reference body/eyes/accessory composition")
    return [ET.tostring(e, encoding="unicode") for e in list(groups[0])[3:]]


def svg_of(content: str) -> str:
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" '
            f'width="200" height="200">{flatten(content)}</svg>')


def point(value: object) -> Point:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"Expected a band point, received {value!r}")
    return number(value[0], "band x") - 100, number(value[1], "band y") - 100


# Validate local inputs before replacing an artboard.
SVGS: dict[str, str] = {}
BAND_POINTS: list[Command] = []
XL, XR = 0.0, 0.0
STREAKS: Record = {}
if NAME == "coral":
    inv = accessory_parts("investigating")
    if len(inv) < 2: raise ValueError("Investigation SVG needs a deerstalker and magnifier")
    SVGS = {"Deerstalker": svg_of(inv[0]), "Magnifier": svg_of("".join(inv[1:]))}
    bands = record(json.loads((GEN / "band_cubics.json").read_text()), "bands")
    band = record(bands[SHAPE], SHAPE)
    XL, XR = number(band["xl"], "xl") - 100, number(band["xr"], "xr") - 100
    BAND_POINTS = [("M", *point(band["start"]))]
    segments = band["segs"]
    if not isinstance(segments, list): raise ValueError("Band segs must be an array")
    for segment in segments:
        if not isinstance(segment, list) or len(segment) != 3:
            raise ValueError(f"Invalid band cubic: {segment!r}")
        BAND_POINTS.append(("C", *point(segment[0]), *point(segment[1]), *point(segment[2])))
elif NAME == "black":
    SVGS = {"HardHat": svg_of("".join(accessory_parts("building")))}
    streaks = record(json.loads((GEN / "streaks.json").read_text()), "streaks")
    STREAKS = record(streaks[NAME], "black streaks")
    for colour in ("coral", "mint", "sky", "lilac", "peach", "aqua"):
        string(STREAKS[colour], "streak colour")

# ---------------------------------------------------------------- rig
AB, SAVED = new_artboard("LandingAgent", CACHE)
ROOT = group("Root", AB, 150, 165, 100)
BODY_ID, VERTS, MORPHS = body(ROOT, SHAPE, BODY)
EYES = group("Eyes", ROOT, 0, EY, 100)
eyes = shapes(EYES, [
    ("EyeL", -30, 0, [fill(EYE_INK)], EYE), ("EyeR", 30, 0, [fill(EYE_INK)], EYE),
    ("EyeArcL", -30, 0, [stroke(EYE_INK, 15.1)], ARC),
    ("EyeArcR", 30, 0, [stroke(EYE_INK, 15.1)], ARC),
])
EYEL, EYER, ARCL, ARCR = (eyes[n] for n in ("EyeL", "EyeR", "EyeArcL", "EyeArcR"))
for arc in (ARCL, ARCR): round_stroke(arc)
set_props({ARCL: {"18": 0}, ARCR: {"18": 0}})
ACC: dict[str, str] = {}
for label, svg in SVGS.items():
    data = (f"data:image/svg+xml;name={label.lower()}.svg;base64,"
            + base64.b64encode(svg.encode()).decode())
    asset = record(api("upload_asset", {"file": data, "name": label.lower()})["asset"], label)
    ACC[label] = string(api("assets_tool", {"command": "addSvgInstance", "data": {
        "addSvgInstance": {"assetId": oid(asset), "name": label,
                           "parentId": ROOT, "x": -100, "y": -100}}})["nodeId"], label)
    set_props({ACC[label]: {"18": 0}})

# (trim id, revolutions, phase), in the reference's draw order.
ORBITS: list[tuple[str, int, int]] = []
if NAME == "coral":
    DEER, MAG = ACC["Deerstalker"], ACC["Magnifier"]
    HEADSET = group("Headset", ROOT, 0, 0, 0)
    boom: list[Command] = [("M", XR + 12, EY + 22),
                          ("C", XR + 14, EY + 48, XR - 16, EY + 56, 30, EY + 54)]
    headset_paths = shapes(HEADSET, [("Band", 0, 0, [stroke(INK, 10)], BAND_POINTS),
                                    ("Boom", 0, 0, [stroke(INK, 4)], boom)])
    for sid in headset_paths.values(): round_stroke(sid)
    parametric(HEADSET, [
        ("CupL", "rectangle", 26, 56, XL - 13, EY, [fill(INK)], {"cornerRadius": 13}),
        ("CupR", "rectangle", 26, 56, XR + 13, EY, [fill(INK)], {"cornerRadius": 13}),
    ])
    MIC = group("Mic", HEADSET, 30, EY + 54, 100)
    parametric(MIC, [("MicRing", "ellipse", 13, 13, 0, 0, [fill(BODY), stroke(INK, 3.5)], {})])
    api("reorder_objects", {"operations": [{"objectId": i, "order": "sendToFront"}
                                           for i in (HEADSET, DEER, MAG)]})
elif NAME == "black":
    HAT = ACC["HardHat"]
    OB = group("OrbitsBack", ROOT, 0, 0, 0)
    OF = group("OrbitsFront", ROOT, 0, 0, 0)
    orbit_specs = ((OB, -25, "coral", 1, 0), (OB, 40, "mint", 2, 55),
                   (OF, 62, "sky", 1, 30), (OB, 8, "lilac", 1, 70),
                   (OB, -60, "peach", 2, 15), (OF, 88, "aqua", 1, 82))
    for i, (parent, tilt, colour, revs, phase) in enumerate(orbit_specs):
        name = f"Orbit{i + 1}"
        col = "#FF" + string(STREAKS[colour], colour).lstrip("#").upper()
        sid = parametric(parent, [(name, "ellipse", 250 + (i % 3) * 14,
                                  92 + (i % 2) * 10, 0, 4, [stroke(col, 7)], {})])[name]
        set_props({sid: {"15": tilt}})
        round_stroke(sid)
        paint = one([o for o in children(sid) if has_type(o, "Stroke")], name)
        trim = one(rows("path_editor", {"command": "addTrimPaths", "data": {
            "addTrimPaths": {"paintIds": [oid(paint)], "start": 0, "end": 100,
                             "mode": "sequential"}}}, "trimPaths"), name)
        ORBITS.append((oid(trim), revs, phase))
    CHECK = group("Check", ROOT, 0, TOP - 130, 0)
    parametric(CHECK, [("CheckDisc", "ellipse", 36, 36, 0, 0, [fill(BODY)], {})])
    tick = shapes(CHECK, [("Tick", 0, 0, [stroke(WHITE, 4.5)],
                           [("M", -9, 0), ("L", -3, 6), ("L", 9, -7)])])["Tick"]
    round_stroke(tick)
    api("reorder_objects", {"operations": [{"objectId": tick, "order": "sendToFront"}]})
    api("reorder_objects", {"operations": [{"objectId": OB, "order": "sendToBack"}]})
    api("reorder_objects", {"operations": [{"objectId": i, "order": "sendToFront"}
                                           for i in (HAT, OF, CHECK)]})
else:
    HANDS = group("Hands", ROOT, 0, 0, 0)
    hands = parametric(HANDS, [
        ("HandL", "ellipse", 34, 34, -62, EY + 30, [fill(BODY), stroke(INK, 3.5)], {}),
        ("HandR", "ellipse", 34, 34, 62, EY + 30, [fill(BODY), stroke(INK, 3.5)], {}),
    ])
    HL, HR = hands["HandL"], hands["HandR"]
    api("reorder_objects", {"operations": [{"objectId": HANDS, "order": "sendToFront"}]})

# ---------------------------------------------------------------- keyframe helpers
def key(obj: str, prop: int, f: int, value: float, interp: str, params: dict[str, float]) -> Keyframe:
    offset = 150 if obj == ROOT and prop == X else 165 if obj == ROOT and prop == Y else 0
    return frame(obj, prop, f, value + offset, interp, params)


def k(obj: str, prop: int, f: int, value: float) -> Keyframe:
    return key(obj, prop, f, value, "cubic", EASE)


def ease(obj: str, prop: int, f: int, value: float, params: dict[str, float]) -> Keyframe:
    return key(obj, prop, f, value, "cubic", params)


def hold(obj: str, prop: int, f: int, value: float) -> Keyframe:
    return key(obj, prop, f, value, "hold", {})


def elastic(obj: str, prop: int, f: int, value: float, amp: float, period: float) -> Keyframe:
    return key(obj, prop, f, value, "elastic", {"amplitude": amp, "period": period, "easing": 1})


def body_pose(f: int, pose: str) -> list[Keyframe]:
    return [k(v, prop, f, value) for v, (x, y) in zip(VERTS, MORPHS[pose], strict=True)
            for prop, value in ((VX, x), (VY, y))]


def neutral(f: int) -> list[Keyframe]:
    ks = [k(ROOT, X, f, 0), k(ROOT, Y, f, 0), k(ROOT, R, f, 0),
          k(ROOT, SX, f, 100), k(ROOT, SY, f, 100), hold(ROOT, OP, f, 100),
          hold(BODY_ID, OP, f, 100), k(BODY_ID, SX, f, 100), k(BODY_ID, SY, f, 100),
          hold(EYES, OP, f, 100), k(EYES, SX, f, 100), k(EYES, SY, f, 100)]
    # Eyes X/Y belong to gaze binding; expression offsets live on the individual eyes.
    for eye, x in ((EYEL, -30), (EYER, 30)):
        ks += [hold(eye, OP, f, 100), k(eye, X, f, x), k(eye, Y, f, 0),
               k(eye, R, f, 0), k(eye, SX, f, 100), k(eye, SY, f, 100)]
    for arc in (ARCL, ARCR):
        ks += [hold(arc, OP, f, 0), k(arc, Y, f, 0), k(arc, SX, f, 100), k(arc, SY, f, 100)]
    for accessory in ACC.values():
        ks += [hold(accessory, OP, f, 0), k(accessory, Y, f, -100), k(accessory, R, f, 0)]
    if NAME == "coral":
        ks += [hold(HEADSET, OP, f, 0), k(HEADSET, Y, f, 0),
               k(MIC, SX, f, 100), k(MIC, SY, f, 100)]
    elif NAME == "black":
        ks += [hold(OB, OP, f, 0), hold(OF, OP, f, 0), hold(CHECK, OP, f, 0),
               k(CHECK, SX, f, 100), k(CHECK, SY, f, 100)]
        ks += [key(trim, prop, f, value, "linear", {}) for trim, _, phase in ORBITS
               for prop, value in ((TS, 0), (TE, 0), (TO, phase))]
    else:
        ks += [hold(HANDS, OP, f, 0)]
        for hand, x in ((HL, -62), (HR, 62)):
            ks += [k(hand, X, f, x), k(hand, Y, f, EY + 30), k(hand, SX, f, 100), k(hand, SY, f, 100)]
    return ks + body_pose(f, "NEUTRAL")


def eyes_shut(f0: int, f1: int) -> list[Keyframe]:
    return [kf for e in (EYEL, EYER) for kf in
            (k(e, SY, f0, 100), ease(e, SY, min(f1, f0 + 4), 8, IN))]


def eyes_open(f0: int, f1: int, to: float) -> list[Keyframe]:
    return [kf for e in (EYEL, EYER) for kf in (k(e, SY, f0, 8), ease(e, SY, f1, to, OUT))]


def eyes_wipe(f0: int, f1: int) -> list[Keyframe]:
    return [hold(EYES, OP, f0, 100)] + [kf for e in (EYEL, EYER) for prop in (SX, SY)
            for kf in (elastic(e, prop, f0, 30, 0.6, 0.6), k(e, prop, f1, 100))]


def blink(f: int) -> list[Keyframe]:
    return [kf for e in (EYEL, EYER) for kf in
            (k(e, SY, f, 100), ease(e, SY, f + 5, 8, IN), ease(e, SY, f + 11, 100, OUT))]


def breathe(n: int, amp: float) -> list[Keyframe]:
    return [k(ROOT, SY, 0, 100), k(ROOT, SY, n // 2, 100 + amp), k(ROOT, SY, n, 100),
            k(ROOT, Y, 0, 0), k(ROOT, Y, n // 2, -amp * 0.6), k(ROOT, Y, n, 0)]


def nod() -> list[Keyframe]:
    ks = [k(ROOT, Y, 0, 0), k(ROOT, Y, 6, 6), k(ROOT, Y, 14, -2), k(ROOT, Y, 28, 0),
          k(ROOT, Y, 36, 0), k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 94),
          k(ROOT, SY, 14, 103), k(ROOT, SY, 28, 100), k(ROOT, SY, 36, 100)]
    for eye, arc in ((EYEL, ARCL), (EYER, ARCR)):
        ks += [hold(eye, OP, 6, 0), hold(arc, OP, 6, 100),
               hold(arc, OP, 28, 0), hold(eye, OP, 28, 100)]
        for prop in (SX, SY): ks += [k(arc, prop, 6, 40), ease(arc, prop, 12, 100, OUT)]
    return ks + [k(ARCR, Y, 6, 0), k(ARCR, Y, 12, -4), k(ARCR, Y, 26, 0)] + eyes_wipe(28, 36)


def orbit_run(f0: int, f1: int, orbit: tuple[str, int, int], extra: int) -> list[Keyframe]:
    trim, revs, phase = orbit
    total = revs * 100 + extra
    ks: list[Keyframe] = []
    for i in range(revs + (1 if extra else 0)):
        fa = f0 + (f1 - f0) * (i * 100) / total
        fb = f0 + (f1 - f0) * min((i + 1) * 100, total) / total
        start = (phase + i * 100) % 100
        # Keep the wrapped endpoint one frame before the next segment's start;
        # duplicate-frame replacement otherwise erases the first revolution.
        end_frame = round(fb) - (1 if (i + 1) * 100 < total else 0)
        ks += [key(trim, TO, round(fa), start, "linear", {}),
               key(trim, TO, end_frame, start + min(100, total - i * 100), "linear", {})]
    return ks


# ---------------------------------------------------------------- animations (neutral first, last key wins)
A: dict[str, Animation] = {}
A["idle"] = (360, True, breathe(360, 2.5)
    + [k(ROOT, SY, 90, 102.5), k(ROOT, SY, 270, 102.5), k(ROOT, Y, 90, -1.5), k(ROOT, Y, 270, -1.5)]
    + blink(110) + blink(296) + blink(306)
    + [k(e, X, f, x + dx) for e, x in ((EYEL, -30), (EYER, 30))
       for f, dx in ((170, 0), (180, 5), (215, 5), (226, 0))]
    + [k(e, SY, f, value) for e in (EYEL, EYER)
       for f, value in ((240, 100), (250, 70), (275, 70), (285, 100))])

if NAME == "coral":
    INVEST = [hold(DEER, OP, 0, 100), k(DEER, Y, 0, -100), hold(MAG, OP, 0, 100),
              k(MAG, Y, 0, -100), k(EYER, Y, 0, -4)]
    HEAD = [hold(HEADSET, OP, 0, 100), k(HEADSET, Y, 0, 0)]
    LISTEN = HEAD + [k(ROOT, R, 0, -5), k(EYEL, X, 0, -26), k(EYER, X, 0, 34)]
    A["investigate_in"] = (48, False, eyes_shut(0, 4) + [
        hold(DEER, OP, 3, 100), elastic(DEER, Y, 3, -160, 0.6, 0.5), k(DEER, Y, 18, -100),
        k(ROOT, SY, 12, 100), k(ROOT, SY, 17, 93), k(ROOT, SY, 26, 100)] + eyes_open(12, 22, 100)
        + [hold(MAG, OP, 22, 100), ease(MAG, Y, 22, -60, OUT), k(MAG, Y, 36, -104),
           k(MAG, Y, 42, -100), k(EYER, Y, 36, 0), k(EYER, Y, 44, -4)])
    A["investigate"] = (150, True, INVEST + [k(MAG, R, 0, 0), k(MAG, R, 40, 4),
        k(MAG, R, 80, -3), k(MAG, R, 150, 0), k(MAG, Y, 40, -102), k(MAG, Y, 80, -100)]
        + [k(e, X, f, x + dx) for e, x in ((EYEL, -30), (EYER, 30))
           for f, dx in ((0, 0), (30, 5), (60, 5), (100, -4), (130, 0))] + breathe(150, 2))
    A["realise"] = (54, False, INVEST + eyes_shut(0, 3)
        + [kf for e in (EYEL, EYER) for kf in (k(e, SY, 6, 8), ease(e, SY, 10, 118, OUT),
            elastic(e, SY, 10, 118, 1.0, 0.5), k(e, SY, 26, 100), k(e, SX, 6, 100),
            ease(e, SX, 10, 118, OUT), elastic(e, SX, 10, 118, 1.0, 0.5), k(e, SX, 26, 100))]
        + [k(ROOT, Y, 6, 0), elastic(ROOT, Y, 10, -4, 1.0, 0.5), k(ROOT, Y, 26, 0),
           k(MAG, Y, 6, -100), elastic(MAG, Y, 10, -110, 1.0, 0.5), k(MAG, Y, 26, -100),
           hold(EYER, OP, 30, 0), hold(ARCR, OP, 30, 100), k(ARCR, Y, 30, -4),
           hold(ARCR, OP, 44, 0), hold(EYER, OP, 44, 100), k(EYER, Y, 54, -4)])
    A["listen_in"] = (36, False, eyes_shut(0, 4) + [
        hold(HEADSET, OP, 3, 100), elastic(HEADSET, Y, 3, -60, 0.7, 0.5), k(HEADSET, Y, 18, 0),
        k(ROOT, SY, 12, 100), k(ROOT, SY, 17, 92), k(ROOT, SY, 26, 100),
        k(ROOT, R, 8, 0), k(ROOT, R, 26, -5)] + eyes_open(12, 22, 100)
        + [k(EYEL, X, 12, -30), k(EYEL, X, 24, -26), k(EYER, X, 12, 30), k(EYER, X, 24, 34)])
    A["listen"] = (150, True, LISTEN + [k(ROOT, R, 75, -3), k(ROOT, R, 150, -5),
        k(ROOT, Y, 0, 0), k(ROOT, Y, 75, -2), k(ROOT, Y, 150, 0)]
        + [k(MIC, prop, f, value) for prop in (SX, SY)
           for f, value in ((0, 100), (38, 118), (75, 100), (113, 118), (150, 100))] + blink(90))
    A["speak"] = (96, True, HEAD
        + [k(ROOT, R, f, value) for f, value in ((0, 0), (24, 2), (48, 0), (72, -2), (96, 0))]
        + [k(obj, prop, start + dt, value) for start in (6, 54)
           for obj, prop, peak in ((ROOT, SY, 103.5), (EYEL, SY, 82), (EYER, SY, 82),
                                   (MIC, SX, 124), (MIC, SY, 124))
           for dt, value in ((0, 100), (10, peak), (22, 100))])
    A["nod"] = (36, False, HEAD + nod())
    A["concern"] = (120, True, HEAD + [k(ROOT, R, 0, 9), k(ROOT, SX, 0, 94),
        k(ROOT, Y, 0, 3), k(EYEL, R, 0, 14), k(EYER, R, 0, -14),
        k(ROOT, R, 60, 7), k(ROOT, R, 120, 9), k(ROOT, Y, 60, 1), k(ROOT, Y, 120, 3)]
        + [k(e, SY, f, value) for e in (EYEL, EYER)
           for f, value in ((0, 75), (50, 75), (55, 8), (61, 75))])
    A["listen_out"] = (18, False, LISTEN + [ease(HEADSET, Y, 0, 0, IN),
        k(HEADSET, Y, 12, -70), hold(HEADSET, OP, 12, 0), k(ROOT, R, 14, 0),
        k(EYEL, X, 14, -30), k(EYER, X, 14, 30), k(ROOT, SY, 0, 100),
        k(ROOT, SY, 6, 104), k(ROOT, SY, 16, 100)])
    A["investigate_out"] = (20, False, INVEST + [ease(DEER, Y, 0, -100, IN),
        k(DEER, Y, 12, -170), hold(DEER, OP, 12, 0), ease(MAG, Y, 0, -100, IN),
        k(MAG, Y, 12, -50), hold(MAG, OP, 12, 0), k(EYER, Y, 12, 0),
        k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 104), k(ROOT, SY, 16, 100)])

elif NAME == "black":
    HATTED = [hold(HAT, OP, 0, 100), k(HAT, Y, 0, -100)]
    BUILD = HATTED + [hold(OB, OP, 0, 100), hold(OF, OP, 0, 100),
        k(EYEL, SY, 0, 60), k(EYER, SY, 0, 60), k(ROOT, R, 0, -14)]
    BUILD += [kf for trim, _, _ in ORBITS for kf in (k(trim, TS, 0, 0), k(trim, TE, 0, STREAK))]
    A["build_in"] = (44, False, eyes_shut(0, 4) + [hold(HAT, OP, 3, 100),
        elastic(HAT, Y, 3, -160, 0.6, 0.5), k(HAT, Y, 18, -100), k(ROOT, SY, 12, 100),
        k(ROOT, SY, 17, 93), k(ROOT, SY, 26, 100), hold(OB, OP, 8, 100), hold(OF, OP, 10, 100)]
        + [kf for i, (trim, _, _) in enumerate(ORBITS) for kf in
           (k(trim, TE, 8 + i * 3, 0), ease(trim, TE, min(44, 30 + i * 3), STREAK, OUT))]
        + [kf for orbit in ORBITS for kf in orbit_run(8, 44, orbit, 25)]
        + eyes_open(12, 22, 60) + [k(ROOT, R, 20, 0), k(ROOT, R, 44, -14)])
    A["build"] = (144, True, BUILD + [k(ROOT, R, 72, 10), k(ROOT, R, 144, -14),
        k(ROOT, Y, 0, 0), k(ROOT, Y, 36, -3), k(ROOT, Y, 72, 0),
        k(ROOT, Y, 108, -3), k(ROOT, Y, 144, 0)]
        + [kf for trim, revs, phase in ORBITS for kf in orbit_run(0, 144, (trim, revs, (phase + 25) % 100), 0)])
    A["repair"] = (120, True, HATTED + [hold(OB, OP, 0, 0), hold(OF, OP, 0, 0),
        k(ROOT, R, 0, 8), k(EYEL, SY, 0, 70), k(EYER, SY, 0, 70),
        k(EYEL, X, 0, -26), k(EYER, X, 0, 34)] + breathe(120, 2)
        + [k(ROOT, prop, start + dt, value) for start in (0, 60)
           for prop, rest, peak in ((X, 0, 6), (SX, 100, 96))
           for dt, value in ((0, rest), (7, peak), (14, rest), (60, rest))])
    A["done"] = (72, False, HATTED + [hold(OB, OP, 0, 0), hold(OF, OP, 0, 0),
        k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 92), k(ROOT, SY, 13, 104), k(ROOT, SY, 20, 100),
        hold(CHECK, OP, 12, 100), elastic(CHECK, SX, 12, 0, 1.0, 0.55), k(CHECK, SX, 28, 100),
        elastic(CHECK, SY, 12, 0, 1.0, 0.55), k(CHECK, SY, 28, 100),
        hold(EYEL, OP, 10, 0), hold(EYER, OP, 10, 0), hold(ARCL, OP, 10, 100), hold(ARCR, OP, 10, 100)]
        + [kf for arc in (ARCL, ARCR) for prop in (SX, SY)
           for kf in (k(arc, prop, 10, 40), ease(arc, prop, 18, 100, OUT))])
    A["build_out"] = (22, False, BUILD + [ease(HAT, Y, 0, -100, IN), k(HAT, Y, 12, -180),
        hold(HAT, OP, 12, 0)] + [kf for trim, _, _ in ORBITS for kf in
        (ease(trim, TS, 0, 0, IN), k(trim, TS, 16, STREAK))]
        + [kf for trim, _, phase in ORBITS for kf in orbit_run(0, 22, (trim, 0, (phase + 25) % 100), 20)]
        + [hold(OB, OP, 17, 0), hold(OF, OP, 17, 0), k(ROOT, R, 16, 0),
           k(EYEL, SY, 14, 100), k(EYER, SY, 14, 100), k(ROOT, SY, 0, 100),
           k(ROOT, SY, 6, 104), k(ROOT, SY, 16, 100)])
    # Preserve the orbit-off pose when leaving repair/done; reuse the same hat lift.
    A["repair_out"] = (22, False, A["build_out"][2] + [hold(OB, OP, 0, 0), hold(OF, OP, 0, 0)])
    A["done_out"] = (22, False, A["repair_out"][2] + [hold(CHECK, OP, 0, 100),
        ease(CHECK, SX, 0, 100, IN), ease(CHECK, SY, 0, 100, IN),
        k(CHECK, SX, 12, 0), k(CHECK, SY, 12, 0), hold(CHECK, OP, 12, 0)])
else:
    PRESENT = [hold(HANDS, OP, 0, 0), hold(HANDS, OP, 1, 100), k(ROOT, R, 18, -4), k(ROOT, Y, 18, -4)]
    A["present"] = (90, False, PRESENT
        + [kf for hand, sign in ((HL, -1), (HR, 1)) for kf in (
            elastic(hand, X, 0, sign * 62, 1.0, 0.5), k(hand, X, 18, sign * 58),
            elastic(hand, Y, 0, EY + 30, 1.0, 0.5), k(hand, Y, 18, EY + 50))]
        + [k(e, Y, f, value) for e in (EYEL, EYER) for f, value in
           ((0, 0), (8, 4), (48, 4), (60, 0), (90, 0))])
    A["present_out"] = (12, False, [hold(HANDS, OP, 0, 100), hold(HANDS, OP, 12, 0),
        k(ROOT, R, 0, -4), k(ROOT, R, 12, 0), k(ROOT, Y, 0, -4), k(ROOT, Y, 12, 0)]
        + [kf for hand, sign in ((HL, -1), (HR, 1)) for kf in (
            ease(hand, X, 0, sign * 58, IN), k(hand, X, 12, sign * 62),
            ease(hand, Y, 0, EY + 50, IN), k(hand, Y, 12, EY + 30))])
    A["nod"] = (36, False, nod())

write_animations(A, neutral(0))

# ---------------------------------------------------------------- view model and converters
enums = rows("viewmodel_editor", {"command": "listDataEnums"}, "dataEnums")
if not any(e["name"] == "LandingPose" for e in enums) and SAVED.get("enum") != "LandingPose":
    api("viewmodel_editor", {"command": "createDataEnums", "data": {
        "createDataEnums": {"dataEnums": [{"name": "LandingPose", "values": list(POSES)}]}}})
save_binding(CACHE, AB, "enum", "LandingPose")
VM, P = view_model(AB, "LandingModel", [
    {"name": "pose", "propertyType": "enum", "enumName": "LandingPose"},
    {"name": "gazeX", "propertyType": "number"}, {"name": "gazeY", "propertyType": "number"},
    {"name": "energy", "propertyType": "number"},
], CACHE, SAVED)
CV = converters(AB, [
    {"converterType": "formula", "name": "landingGazeToX", "formula": "{Input} * 12"},
    {"converterType": "formula", "name": "landingGazeToY", "formula": f"{{Input}} * 12 + {EY}"},
    {"converterType": "formula", "name": "landingEnergyToScale", "formula": "100 + {Input} * 3"},
], CACHE, SAVED)
api("viewmodel_editor", {"command": "databind", "data": {"databind": {
    "viewModelId": VM, "bindings": [
        {"objectId": EYES, "propertyKey": X, "viewModelPropertyId": P["gazeX"], "converterId": CV["landingGazeToX"]},
        {"objectId": EYES, "propertyKey": Y, "viewModelPropertyId": P["gazeY"], "converterId": CV["landingGazeToY"]},
        {"objectId": ROOT, "propertyKey": SY, "viewModelPropertyId": P["energy"], "converterId": CV["landingEnergyToScale"]},
    ]}}})

# ---------------------------------------------------------------- state machine / transition table
sm = one(rows("animation_editor", {"command": "listStateMachines"}, "stateMachines"), "default machine")
api("rename_objects", {"renames": [{"id": oid(sm), "name": "LandingMachine"}]})
layer = one(rows("animation_editor", {"command": "queryStateMachine", "data": {
    "queryStateMachine": {"stateMachineId": oid(sm)}}}, "layers"), "default layer")
default = one([s for s in records(layer["states"], "default states") if s["type"] == "animation"], "idle state")
RETURNS: dict[str, str] = {"coral": {"realise": "investigate", "nod": "listen"},
                         "black": {}, "mint": {"nod": "idle"}}[NAME]
state_anims = {n: n if n in A else "idle" for n in POSES if n != "idle"}
state_anims.update({n: n for n in A if n not in POSES})
# These resting aliases consume a held one-shot request. They use the family's
# actual resting animation, then rejoin normal routing when pose changes.
state_anims.update({pose + "_rest": resting for pose, resting in RETURNS.items()})
made = rows("animation_editor", {"command": "createStates", "data": {"createStates": {
    "layerId": layer["layerId"], "states": [{"name": n, "linearAnimationName": anim,
        "x": 400 + 220 * (i // 8), "y": -400 + 100 * (i % 8)}
        for i, (n, anim) in enumerate(state_anims.items())]}}}, "states")
SID = {string(s["name"], "state name"): oid(s) for s in made if s["type"] == "animation"}
SID["idle"] = oid(default)
FAMILIES: tuple[tuple[str, ...], ...] = {
    "coral": (("investigate", "realise"), ("listen", "speak", "nod", "concern")),
    "black": (("build", "repair", "done"),), "mint": (("present",),),
}[NAME]
FAMILY = {pose: members for members in FAMILIES for pose in members}
OUTS = {pose: members[0] + "_out" for pose, members in FAMILY.items()}
if NAME == "black": OUTS.update(repair="repair_out", done="done_out")
# (from, to, kind, pose values, duration); conditions within one entry are ANDed.
Transition = tuple[str, str, str, tuple[str, ...], int]
T: list[Transition] = []
for pose in POSES[1:]:
    entry = pose + "_in" if pose + "_in" in A else pose
    T.append(("idle", entry, "equal", (pose,), 100))
    if entry != pose:
        T.append((entry, pose, "exit", (), 0))
        T.append((entry, OUTS[pose], "notEqual", FAMILY[pose], 160))
    if pose in FAMILY:
        for target in FAMILY[pose]:
            if target != pose: T.append((pose, target, "equal", (target,), 160))
        T.append((pose, OUTS[pose], "notEqual", FAMILY[pose], 160))
    elif pose not in RETURNS:
        T.append((pose, "idle", "notEqual", (pose,), 160))
for out in dict.fromkeys(OUTS.values()): T.append((out, "idle", "exit", (), 0))
for pose in RETURNS:
    rest = pose + "_rest"
    T.append((pose, rest, "exit", (), 160))
    if pose in FAMILY:
        for target in FAMILY[pose]:
            if target != pose: T.append((rest, target, "equal", (target,), 160))
        T.append((rest, OUTS[pose], "notEqual", FAMILY[pose], 160))
    else:
        T.append((rest, "idle", "notEqual", (pose,), 160))

by_from: dict[str, list[Transition]] = {}
for transition in T: by_from.setdefault(transition[0], []).append(transition)
created = rows("animation_editor", {"command": "createTransitions", "data": {"createTransitions": {
    "states": [{"id": SID[source], "transitions": [{"to": SID[t]} for _, t, _, _, _ in transitions]}
               for source, transitions in by_from.items()]}}}, "transitions")
# The renamed default animation state can report an empty name.
transition_ids: dict[tuple[str, str], str] = {}
for item in created:
    source = string(item["from"], "transition from") or "idle"
    target = string(item["to"], "transition to") or "idle"
    if (source, target) in transition_ids:
        raise ValueError(f"Duplicate transition {source} -> {target}: {created!r}")
    transition_ids[(source, target)] = oid(item)
conditions: list[Record] = []
properties: dict[str, dict[str, float | bool]] = {}
for source, target, kind, values, duration in T:
    tid = transition_ids[(source, target)]
    properties[tid] = {"158": duration}
    if kind == "exit": properties[tid].update({"152": 12, "160": 100})
    else:
        conditions.append({"id": tid, "conditions": [
            {"leftComparator": {"viewModelPropertyId": P["pose"]}, "comparationOperation": kind,
             "rightComparator": {"value": value, "valueType": "constantValueType"}} for value in values]})
api("animation_editor", {"command": "createConditions", "data": {
    "createConditions": {"transitions": conditions}}})
set_props(properties)
print("built LandingAgent", NAME, "—", len(T), "transitions; energy bound to Root.scaleY")
export_rig(f"utrace-landing-{NAME}.riv")
