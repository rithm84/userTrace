"""uTrace agent character, version 2: fourteen official states, Grok-style motion (anticipation, overshoot, in/loop/out phases).
Usage: python3 build_v2.py <colour> [--resume]   Builds into the OPEN, EMPTY Rive file through the MCP HTTP endpoint and exports the .riv."""
import base64, json, re, sys, os, math
from mcp import call
import morph
from flatten import flatten
NAME = [a for a in sys.argv[1:] if not a.startswith("--")][0]
SVGDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "source", "vectors")
BANDS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "band_cubics.json")))
EXPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "packages", "utrace-agent-character", "assets")
INK, WHITE = "#FF111111", "#FFFFFFFF"
def argb(hx): return "#FF" + hx.lstrip("#").upper()
C = {"coral":  dict(colour="#FCADA3", shape="circle",   eye_y=105, top=12, bubble=(52.0, -82), dark="#B65047"),
     "black":  dict(colour="#111111", shape="square",   eye_y=105, top=18, bubble=(54.6, -76), dark="#8A8A8A"),
     "mint":   dict(colour="#97D6A4", shape="hexagon",  eye_y=105, top=3,  bubble=(52.0, -74), dark="#268A48"),
     "sky":    dict(colour="#85CEF9", shape="bean",     eye_y=100, top=23, bubble=(49.4, -78), dark="#007EB7"),
     "lilac":  dict(colour="#CDB6F9", shape="triangle", eye_y=118, top=7,  bubble=(49.4, -54), dark="#815FB7"),
     "peach":  dict(colour="#F5B488", shape="pill",     eye_y=92,  top=12, bubble=(40.6, -76), dark="#AF5B09"),
     "aqua":   dict(colour="#72D8D7", shape="arch",     eye_y=104, top=18, bubble=(52.0, -72), dark="#008C8D")}[NAME]
DARKS = {"coral": "#B65047", "mint": "#268A48", "sky": "#007EB7", "lilac": "#815FB7", "peach": "#AF5B09", "aqua": "#008C8D"}
STREAKS = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "streaks.json")))[NAME]   # partner body colours, darkened only where they sit too close to this body
RIBBONS = [n for n in ("coral", "black", "mint", "sky", "lilac", "peach", "aqua") if n != NAME]
BLACK = NAME == "black"; BODY = argb(C["colour"]); DARK = argb(C["dark"]); EYE_INK = WHITE if BLACK else INK; TICK = WHITE if BLACK else INK
EY = C["eye_y"] - 100; SHAPE = C["shape"]
X, Y, R, SX, SY, OP, PW, PH, TS, TE = 13, 14, 15, 16, 17, 18, 20, 21, 114, 115
STATES = ["idle", "investigating", "listening", "thinking", "speaking", "acknowledgement", "concern", "building", "completion", "ship", "reconnecting", "paused", "sleep", "pointer"]
def ok(r, what):
    if not (isinstance(r, dict) and r.get("success")): raise SystemExit(f"{what} failed: {r}")
    return r
def fill(color): return {"paintType": "fill", "color": color}
def stroke(color, w): return {"paintType": "stroke", "color": color, "width": w}
def cmds(points):
    out = []
    for p in points:
        if p[0] == "M": out.append({"commandType": "moveTo", "x": p[1], "y": p[2]})
        elif p[0] == "L": out.append({"commandType": "lineTo", "x": p[1], "y": p[2]})
        elif p[0] == "C": out.append({"commandType": "cubicTo", "control1X": p[1], "control1Y": p[2], "control2X": p[3], "control2Y": p[4], "endX": p[5], "endY": p[6]})
        elif p[0] == "Z": out.append({"commandType": "close"})
    return out
def set_props(m): ok(call("set_property_values", {"propertyValues": m}), "set_property_values")
def shape_id(name): return [o for o in call("find_objects", {"name": name, "type": "shape"})["objects"] if o["name"] == name][0]["id"]
def children(obj, depth=1): return call("query_objects", {"objectIds": [obj], "depth": depth})["objects"]
def paint_ids(shape):
    return [o["id"] for o in children(shape) if "Stroke" in o["types"] or "Fill" in o["types"]]
def round_stroke(shape):
    for o in children(shape):
        if "Stroke" in o["types"]: set_props({o["id"]: {"48": 1, "49": 1}})
def trim(shape):
    sid = [o["id"] for o in children(shape) if "Stroke" in o["types"]][0]
    return ok(call("path_editor", {"command": "addTrimPaths", "data": {"addTrimPaths": {"paintIds": [sid], "start": 0, "end": 100, "mode": "sequential"}}}), "trim")["trimPaths"][0]["id"]
def group(name, parent, x=0, y=0, op=0):
    g = ok(call("group_editor", {"name": name, "parentId": parent}), name)["group"]["id"]; set_props({g: {"13": x, "14": y, "18": op}}); return g
def shapes(parent, specs):
    """createShapes for freeform items: specs = [(name, x, y, paints, points)] -> ids by name. Paths without a close command are left open."""
    ok(call("path_editor", {"command": "createShapes", "data": {"createShapes": {"shapes": [{"name": n, "parentId": parent, "x": x, "y": y, "paints": p, "paths": [{"name": "p", "commands": cmds(pts)}]} for n, x, y, p, pts in specs]}}}), "shapes")
    out = {n: shape_id(n) for n, *_ in specs}
    open_paths = {}
    for n, x, y, p, pts in specs:
        if pts[-1][0] != "Z":
            for o in children(out[n]):
                if "PointsPath" in o["types"]: open_paths[o["id"]] = {"32": False}
    if open_paths: set_props(open_paths)
    return out
def parametric(parent, specs):
    """specs = [(name, primitive, w, h, x, y, paints, extra)] -> ids by name"""
    r = call("path_editor", {"command": "createParametricShapes", "data": {"createParametricShapes": {"shapes": [{"primitive": pr, "name": n, "width": w, "height": h, "x": x, "y": y, "parentId": parent, "paints": p, **ex} for n, pr, w, h, x, y, p, ex in specs]}}})
    ok(r, "parametric"); return {s["name"]: s["id"] for s in r["shapes"]}, {s["name"]: s["pathId"] for s in r["shapes"]}

RESUME = "--resume" in sys.argv
def build_rig():
    for a in call("list_artboards", {}).get("artboards", []):
        if a["name"] == "AgentCharacter": call("delete_objects", {"objectIds": [a["id"]]})
    ab = ok(call("open_file_editor", {"command": "createArtboard", "data": {"createArtboard": [{"name": "AgentCharacter", "width": 300, "height": 300}]}}), "createArtboard")["artboards"][0]["id"]
    call("open_file_editor", {"command": "focusArtboard", "data": {"focusArtboard": {"artboardId": ab, "fitToViewport": True}}})
    for o in call("get_artboard_hierarchy", {"artboardId": ab, "depth": 1})["objects"]:
        if "Fill" in o["types"]: call("delete_objects", {"objectIds": [o["id"]]})
    root = group("Root", ab, 150, 165, 100)
    ids = {"ab": ab, "root": root}
    # ---- body: a dense polygon of straight vertices with corner rounding (only x/y need keys), sampled by polar angle so it can morph
    bpath = None; dp = morph.dense(SHAPE, 120)["NEUTRAL"]
    cmd = [("M", dp[0][0], dp[0][1])] + [("L", x, y) for x, y in dp[1:]] + [("Z",)]
    body = shapes(root, [("Body", 0, 0, [fill(BODY)], cmd)])["Body"]
    bp = [o["id"] for o in children(body) if "PointsPath" in o["types"]][0]
    ids["verts"] = [o["id"] for o in children(bp) if any("Vertex" in t for t in o["types"])]
    assert len(ids["verts"]) == len(dp), (len(ids["verts"]), len(dp))
    sharp = set(morph.CORNERS.get(SHAPE, []))
    set_props({vid: {"26": 0 if (round(x), round(y)) in {(round(cx), round(cy)) for cx, cy in sharp} else 8} for vid, (x, y) in zip(ids["verts"], dp)})
    ids.update(body=body, bpath=bpath)
    # ---- eyes (ovals + arc variants), wrapped in a group for gaze binding
    EYE = [("M", 20.79, 0), ("C", 20.79, 21.168, 12.474, 35.28, 0, 35.28), ("C", -12.474, 35.28, -20.79, 21.168, -20.79, 0), ("C", -20.79, -21.168, -12.474, -35.28, 0, -35.28), ("C", 12.474, -35.28, 20.79, -21.168, 20.79, 0), ("Z",)]
    ARC = [("M", -22.05, 8.4), ("C", -14.7, -8.9, 14.7, -8.9, 22.05, 8.4)]
    eyes = group("Eyes", root, 0, EY, 100)
    e = shapes(eyes, [("EyeL", -30, 0, [fill(EYE_INK)], EYE), ("EyeR", 30, 0, [fill(EYE_INK)], EYE), ("EyeArcL", -30, 0, [stroke(EYE_INK, 15.1)], ARC), ("EyeArcR", 30, 0, [stroke(EYE_INK, 15.1)], ARC)])
    for a in ("EyeArcL", "EyeArcR"): round_stroke(e[a])
    set_props({e["EyeArcL"]: {"18": 0}, e["EyeArcR"]: {"18": 0}})
    ids.update(eyes=eyes, eyeL=e["EyeL"], eyeR=e["EyeR"], arcL=e["EyeArcL"], arcR=e["EyeArcR"])
    # ---- traced accessories: hats, magnifier, stamp, satchel (split from the composed state files)
    def inner(state):
        s = open(f"{SVGDIR}/acc-{NAME}-{state}.svg").read(); return re.search(r'<g transform="translate\(22 40\) scale\(0\.78\)">(.*)</g></svg>$', s, re.S).group(1)
    def top_level(s):
        els = []; i = 0; n = len(s)
        while i < n:
            if s[i] != "<": i += 1; continue
            tag = re.match(r'<(\w+)', s[i:]).group(1); j = s.index(">", i)
            if s[j - 1] == "/": els.append(s[i:j + 1]); i = j + 1; continue
            depth = 1; k = j + 1
            while depth:
                o = s.find(f"<{tag}", k); c = s.find(f"</{tag}>", k)
                if o != -1 and o < c:
                    jj = s.index(">", o)
                    if s[jj - 1] != "/": depth += 1
                    k = jj + 1
                else: depth -= 1; k = c + len(tag) + 3
            els.append(s[i:k]); i = k
        return els
    def parts(state): return top_level(inner(state))[3:]
    def svg_of(content): return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="200" height="200">{flatten(content)}</svg>'   # baked transforms, arcs as cubics: nothing for the importer to reinterpret
    inv, bld, val, shp = parts("investigating"), parts("building"), parts("validating"), parts("shipping")
    SVGS = {"Deerstalker": svg_of(inv[0]), "Magnifier": svg_of("".join(inv[1:])), "HardHat": svg_of("".join(bld)), "Stamp": svg_of("".join(val)), "Satchel": svg_of("".join(shp))}
    for label, svg in SVGS.items():
        data = "data:image/svg+xml;name=" + label.lower() + ".svg;base64," + base64.b64encode(svg.encode()).decode()
        asset = ok(call("upload_asset", {"file": data, "name": label.lower()}), "upload " + label)["asset"]["id"]
        ids[label] = ok(call("assets_tool", {"command": "addSvgInstance", "data": {"addSvgInstance": {"assetId": asset, "name": label, "parentId": root, "x": -100, "y": -100}}}), label)["nodeId"]
    # ---- headset: clean vectors (cups, cubic band, boom, mic) fitted to this body
    b = BANDS[SHAPE]; xl, xr = b["xl"] - 100, b["xr"] - 100
    band_pts = [("M", b["start"][0] - 100, b["start"][1] - 100)] + [("C", c1[0] - 100, c1[1] - 100, c2[0] - 100, c2[1] - 100, p[0] - 100, p[1] - 100) for c1, c2, p in b["segs"]]
    boom = [("M", xr + 12, EY + 22), ("C", xr + 14, EY + 48, xr - 16, EY + 56, 30, EY + 54)]      # ends under the mic's opaque fill
    HS = group("Headset", root, 0, 0, 0)
    if BLACK:   # white rim under the ink
        shapes(HS, [("BandRim", 0, 0, [stroke(WHITE, 15)], band_pts), ("BoomRim", 0, 0, [stroke(WHITE, 8)], boom)])
        for n in ("BandRim", "BoomRim"): round_stroke(shape_id(n))
        parametric(HS, [("CupRimL", "rectangle", 32, 62, xl - 13, EY, [fill(WHITE)], {"cornerRadius": 16}), ("CupRimR", "rectangle", 32, 62, xr + 13, EY, [fill(WHITE)], {"cornerRadius": 16})])
    hs = shapes(HS, [("Band", 0, 0, [stroke(INK, 10)], band_pts), ("Boom", 0, 0, [stroke(INK, 4)], boom)])
    for n in ("Band", "Boom"): round_stroke(hs[n])
    cups, _ = parametric(HS, [("CupL", "rectangle", 26, 56, xl - 13, EY, [fill(INK)], {"cornerRadius": 13}), ("CupR", "rectangle", 26, 56, xr + 13, EY, [fill(INK)], {"cornerRadius": 13})])
    # the mic is a group (so the pulse scales rim, ring and fill together); the ring's body-colour fill hides the boom end at any scale
    MIC = group("Mic", HS, 30, EY + 54, 100)
    if BLACK: parametric(MIC, [("MicRimDisc", "ellipse", 20, 20, 0, 0, [fill(WHITE)], {})])
    parametric(MIC, [("MicRing", "ellipse", 13, 13, 0, 0, [fill(BODY), stroke(INK, 3.5)], {})])
    ids.update(headset=HS, mic=MIC)
    # ---- vector accessories
    bx, by = C["bubble"]
    BUBBLE = group("Bubble", root, bx, by); CHECK = group("Check", root, 0, C["top"] - 130); DOTS = group("Dots", ab, 150, 165); TRAIL = group("Trail", ab, 84, 169)
    # building orbits: comet streaks (trim paths) on tilted ellipses; two orbit behind the head, one in front
    OB = group("OrbitsBack", root, 0, 0, 0); OF = group("OrbitsFront", root, 0, 0, 0)
    call("reorder_objects", {"operations": [{"objectId": OB, "order": "sendToBack"}]})
    # six orbits, one per partner colour: (name, parent, tilt, colour, revolutions per loop, phase %); four behind the head, two in front
    ORB_SPEC = [("Orbit1", OB, -25, RIBBONS[0], 1, 0), ("Orbit2", OB, 40, RIBBONS[1], 2, 55), ("Orbit3", OF, 62, RIBBONS[2], 1, 30),
                ("Orbit4", OB, 8, RIBBONS[3], 1, 70), ("Orbit5", OB, -60, RIBBONS[4], 2, 15), ("Orbit6", OF, 88, RIBBONS[5], 1, 82)]
    orbits = []
    for i, (n, parent, tilt, col, revs, phase) in enumerate(ORB_SPEC):
        sh, _ = parametric(parent, [(n, "ellipse", 250 + (i % 3) * 14, 92 + (i % 2) * 10, 0, 4, [stroke(argb(STREAKS[col]), 7)], {})]); sid = sh[n]
        set_props({sid: {"15": tilt}}); round_stroke(sid); orbits.append({"shape": sid, "trim": trim(sid), "revs": revs, "phase": phase})
    ids["orbits"] = orbits; ids["ob"] = OB; ids["of"] = OF
    BOX = [("M", -36, -22), ("L", 36, -22), ("C", 41.52, -22, 46, -17.52, 46, -12), ("L", 46, 8), ("C", 46, 13.52, 41.52, 18, 36, 18), ("L", -4, 18), ("L", -18, 30), ("L", -16, 18), ("L", -36, 18), ("C", -41.52, 18, -46, 13.52, -46, 8), ("L", -46, -12), ("C", -46, -17.52, -41.52, -22, -36, -22), ("Z",)]
    shapes(BUBBLE, [("BubbleBox", 0, 0, [fill(WHITE), stroke(INK, 3)], BOX)])
    bd, _ = parametric(BUBBLE, [(f"BubbleDot{i}", "ellipse", 9, 9, x, -2, [fill(INK)], {}) for i, x in ((1, -16), (2, 0), (3, 16))])
    cd, _ = parametric(CHECK, [("CheckDisc", "ellipse", 36, 36, 0, 0, [fill(BODY)], {})])
    tk = shapes(CHECK, [("Tick", 0, 0, [stroke(TICK, 4.5)], [("M", -9, 0), ("L", -3, 6), ("L", 9, -7)])]); round_stroke(tk["Tick"])
    call("reorder_objects", {"operations": [{"objectId": tk["Tick"], "order": "sendToFront"}]})
    tiny = morph.dense(SHAPE, 120)["DOT"][::3]
    dcmd = [("M", tiny[0][0], tiny[0][1])] + [("L", x, y) for x, y in tiny[1:]] + [("Z",)]
    dots = shapes(DOTS, [(f"Dot{i}", x, 4, [fill(BODY)], dcmd) for i, x in ((1, -44), (2, 0), (3, 44))])
    for d in dots.values():
        for o in children(d):
            if "PointsPath" in o["types"]: set_props({v["id"]: {"26": 3} for v in children(o["id"]) if any("Vertex" in t for t in v["types"])})
    tr = shapes(TRAIL, [("Trail1", 0, 0, [stroke("#CC" + C["dark"].lstrip("#"), 4)], [("M", -8, 0), ("L", -30, -7)]), ("Trail2", 0, 0, [stroke("#CC" + C["dark"].lstrip("#"), 3)], [("M", -8, 0), ("L", -44, 0)]), ("Trail3", 0, 0, [stroke("#CC" + C["dark"].lstrip("#"), 2)], [("M", -8, 0), ("L", -58, 7)])])
    for n in tr.values(): round_stroke(n)
    ex, _ = parametric(ab, [("ExclaimDot", "ellipse", 24, 24, 150, 261, [fill(BODY)], {})]); set_props({ex["ExclaimDot"]: {"18": 0}})
    PARTS = group("Particles", ab, 150, 165, 0)
    pa, _ = parametric(PARTS, [("Particle1", "ellipse", 8, 8, 26, -6, [fill(BODY)], {}), ("Particle2", "ellipse", 5, 5, 40, -24, [fill(BODY)], {})])
    ids.update(bubble=BUBBLE, bd1=bd["BubbleDot1"], bd2=bd["BubbleDot2"], bd3=bd["BubbleDot3"], check=CHECK, dots=DOTS, d1=dots["Dot1"], d2=dots["Dot2"], d3=dots["Dot3"],
               trail=TRAIL, trimT=[trim(t) for t in tr.values()], excl=ex["ExclaimDot"], parts=PARTS, p1=pa["Particle1"], p2=pa["Particle2"])
    front = [ids["Satchel"], HS, ids["HardHat"], ids["Deerstalker"], OF, CHECK, BUBBLE, ids["Stamp"], ids["Magnifier"]]
    call("reorder_objects", {"operations": [{"objectId": OB, "order": "sendToBack"}]})
    call("reorder_objects", {"operations": [{"objectId": i, "order": "sendToFront"} for i in (ex["ExclaimDot"], TRAIL, DOTS, PARTS)]})
    call("reorder_objects", {"operations": [{"objectId": i, "order": "sendToFront"} for i in front]})
    set_props({ids[k]: {"18": 0} for k in ("Deerstalker", "Magnifier", "HardHat", "Stamp", "Satchel")})
    json.dump(ids, open(f"ids_{NAME}.json", "w")); return ids
ids = json.load(open(f"ids_{NAME}.json")) if RESUME else build_rig()
I = ids
# ---------------------------------------------------------------- animations
EASE = {"x1": 0.42, "y1": 0.0, "x2": 0.58, "y2": 1.0}; OUT = {"x1": 0.16, "y1": 0.84, "x2": 0.3, "y2": 1.0}; IN = {"x1": 0.55, "y1": 0.0, "x2": 0.8, "y2": 0.25}; SNAP = {"x1": 0.2, "y1": 0.0, "x2": 0.2, "y2": 1.0}
RX, RY = 150, 165
def k(obj, key, frame, value, interp="cubic", params=None):
    if obj == I["root"] and key == X: value += RX
    if obj == I["root"] and key == Y: value += RY
    d = {"objectId": obj, "propertyKey": key, "frame": frame, "value": value, "interpolationType": interp}
    if interp == "cubic": d["cubicParams"] = params or EASE
    if interp == "elastic": d["elasticParams"] = params or {"amplitude": 1.0, "period": 0.5, "easing": 1}
    return d
def hold(obj, key, frame, value): return k(obj, key, frame, value, "hold")
def elastic(obj, key, frame, value, amp=1.0, period=0.5): return k(obj, key, frame, value, "elastic", {"amplitude": amp, "period": period, "easing": 1})
ROOT, BODY_ID, EYES, EYEL, EYER, ARCL, ARCR = I["root"], I["body"], I["eyes"], I["eyeL"], I["eyeR"], I["arcL"], I["arcR"]
DEER, MAG, HAT, STAMP, SATCHEL, HEADSET, MIC = I["Deerstalker"], I["Magnifier"], I["HardHat"], I["Stamp"], I["Satchel"], I["headset"], I["mic"]
BUBBLE, BD1, BD2, BD3, CHECK, DOTS, D1, D2, D3, TRAIL, PARTS, EXCL, P1, P2 = (I[n] for n in ("bubble", "bd1", "bd2", "bd3", "check", "dots", "d1", "d2", "d3", "trail", "parts", "excl", "p1", "p2"))
TRT = I["trimT"]; ORBITS = I["orbits"]; OB, OF = I["ob"], I["of"]; TS_, TE_, TO_ = 114, 115, 116; STREAK = 30
VX, VY = 24, 25
POSES = morph.dense(SHAPE, 120); VERTS = I["verts"]
def body_pose(frame, pose, interp="cubic", params=None):
    """Key every body vertex position to a morph target (dense polygons share angles, so vertex i maps to vertex i)."""
    return [k(vid, key, frame, val, interp, params) for vid, (x, y) in zip(VERTS, POSES[pose]) for key, val in ((VX, x), (VY, y))]
def neutral(f=0):
    ks = [k(ROOT, X, f, 0), k(ROOT, Y, f, 0), k(ROOT, R, f, 0), k(ROOT, SX, f, 100), k(ROOT, SY, f, 100), hold(ROOT, OP, f, 100), hold(BODY_ID, OP, f, 100), hold(EYES, OP, f, 100), k(EYES, SX, f, 100), k(EYES, SY, f, 100)]
    for e, x in ((EYEL, -30), (EYER, 30)): ks += [hold(e, OP, f, 100), k(e, SX, f, 100), k(e, SY, f, 100), k(e, R, f, 0), k(e, X, f, x), k(e, Y, f, 0)]
    for e in (ARCL, ARCR): ks += [hold(e, OP, f, 0), k(e, Y, f, 0), k(e, SX, f, 100), k(e, SY, f, 100)]
    for a in (DEER, MAG, HAT, STAMP, SATCHEL, HEADSET, BUBBLE, CHECK, DOTS, OB, OF, TRAIL, PARTS, EXCL): ks.append(hold(a, OP, f, 0))
    for o in ORBITS: ks += [k(o["trim"], TS_, f, 0), k(o["trim"], TE_, f, 0), k(o["trim"], TO_, f, o["phase"])]
    ks += [k(DEER, Y, f, -100), k(MAG, Y, f, -100), k(STAMP, Y, f, -100), k(HAT, Y, f, -100), k(SATCHEL, X, f, -100), k(HEADSET, Y, f, 0), k(CHECK, SX, f, 100), k(CHECK, SY, f, 100), k(BUBBLE, SX, f, 100), k(BUBBLE, SY, f, 100),
           k(MIC, SX, f, 100), k(MIC, SY, f, 100), k(EXCL, SX, f, 100), k(EXCL, SY, f, 100), k(EXCL, Y, f, 261), k(MAG, R, f, 0), k(DOTS, Y, f, 165), k(TRAIL, X, f, 84), k(BODY_ID, SX, f, 100), k(BODY_ID, SY, f, 100)]
    ks += [k(D1, X, f, -44), k(D3, X, f, 44), k(D2, SX, f, 100), k(D2, SY, f, 100)] + [hold(d, OP, f, 100) for d in (D1, D2, D3)]
    ks += [k(t, TE, f, 0) for t in TRT] + [hold(b, OP, f, 100) for b in (BD1, BD2, BD3)]
    ks += [k(P1, X, f, 26), k(P1, Y, f, -6), k(P2, X, f, 40), k(P2, Y, f, -24), hold(P1, OP, f, 100), hold(P2, OP, f, 100), k(PARTS, Y, f, 165)]
    return ks + body_pose(f, "NEUTRAL")
def eyes_shut(f0, f1):
    f1 = min(f1, f0 + 4); return [k(EYEL, SY, f0, 100), k(EYEL, SY, f1, 8, "cubic", IN), k(EYER, SY, f0, 100), k(EYER, SY, f1, 8, "cubic", IN)]
def eyes_open(f0, f1, to=100): return [k(EYEL, SY, f0, 8), k(EYEL, SY, f1, to, "cubic", OUT), k(EYER, SY, f0, 8), k(EYER, SY, f1, to, "cubic", OUT)]
def eyes_wipe(f0, f1):   # eyes grow back from small dashes
    return [hold(EYES, OP, f0, 100)] + [x for e in (EYEL, EYER) for x in (k(e, SX, f0, 30), elastic(e, SX, f0, 30, 0.6, 0.6), k(e, SX, f1, 100), k(e, SY, f0, 30), elastic(e, SY, f0, 30, 0.6, 0.6), k(e, SY, f1, 100))]
def blink(f): return [k(EYEL, SY, f, 100), k(EYEL, SY, f + 5, 8, "cubic", IN), k(EYEL, SY, f + 11, 100, "cubic", OUT), k(EYER, SY, f, 100), k(EYER, SY, f + 5, 8, "cubic", IN), k(EYER, SY, f + 11, 100, "cubic", OUT)]
def breathe(n, amp=2.5):
    ks = []; half = n // 2
    return [k(ROOT, SY, 0, 100), k(ROOT, SY, half, 100 + amp), k(ROOT, SY, n, 100), k(ROOT, Y, 0, 0), k(ROOT, Y, half, -amp * 0.6), k(ROOT, Y, n, 0)]
def to_dot(f0, f1):   # body morphs through the teardrop into the dot
    mid = (f0 + f1) // 2
    return body_pose(f0, "NEUTRAL", "cubic", IN) + body_pose(mid, "DROP", "cubic", IN) + body_pose(f1, "DOT", "cubic", OUT)
def from_dot(f0, f1):  # dot grows back through the teardrop with one overshoot on the root
    mid = f0 + (f1 - f0) // 3
    return (body_pose(f0, "DOT", "cubic", OUT) + body_pose(mid, "DROP", "cubic", OUT) + body_pose(f1, "NEUTRAL")
            + [k(ROOT, SX, f1 - 4, 100), k(ROOT, SX, f1 + 3, 106), k(ROOT, SX, f1 + 10, 100), k(ROOT, SY, f1 - 4, 100), k(ROOT, SY, f1 + 3, 106), k(ROOT, SY, f1 + 10, 100)])
A, DUR, LOOP = {}, {}, set()
def anim(name, frames, loop, keys): A[name] = keys; DUR[name] = frames; (LOOP.add(name) if loop else None)
# idle: breathing, blinks, a glance and a squint over 6 s
anim("idle", 360, True, breathe(360) + [k(ROOT, SY, 90, 102.5), k(ROOT, SY, 270, 102.5), k(ROOT, Y, 90, -1.5), k(ROOT, Y, 270, -1.5)] + blink(110) + blink(296) + blink(306)
     + [k(EYEL, X, 170, -30), k(EYEL, X, 180, -25), k(EYEL, X, 215, -25), k(EYEL, X, 226, -30), k(EYER, X, 170, 30), k(EYER, X, 180, 35), k(EYER, X, 215, 35), k(EYER, X, 226, 30)]
     + [k(EYEL, SY, 240, 100), k(EYEL, SY, 250, 70), k(EYEL, SY, 275, 70), k(EYEL, SY, 285, 100), k(EYER, SY, 240, 100), k(EYER, SY, 250, 70), k(EYER, SY, 275, 70), k(EYER, SY, 285, 100)])
# listening: headset drops in, lands with a squash, lean toward the speaker; loop bobs and pulses the mic
LISTEN = [hold(HEADSET, OP, 0, 100), k(HEADSET, Y, 0, 0), k(ROOT, R, 0, -7), k(EYEL, X, 0, -26), k(EYER, X, 0, 34)]
anim("listening_in", 36, False, eyes_shut(0, 4) + [hold(HEADSET, OP, 3, 100), elastic(HEADSET, Y, 3, -60, 0.7, 0.5), k(HEADSET, Y, 18, 0), k(ROOT, SY, 12, 100), k(ROOT, SY, 17, 92), k(ROOT, SY, 26, 100), k(ROOT, R, 8, 0), k(ROOT, R, 26, -7)]
     + eyes_open(12, 22) + [k(EYEL, X, 12, -30), k(EYEL, X, 24, -26), k(EYER, X, 12, 30), k(EYER, X, 24, 34)])
anim("listening", 120, True, LISTEN + [k(ROOT, R, 60, -5), k(ROOT, R, 120, -7), k(ROOT, Y, 0, 0), k(ROOT, Y, 60, -2), k(ROOT, Y, 120, 0)]
     + [x for key in (SX, SY) for x in (k(MIC, key, 0, 100), k(MIC, key, 30, 130), k(MIC, key, 60, 100), k(MIC, key, 90, 130), k(MIC, key, 120, 100))] + blink(80))
# thinking: eyes shut, body collapses to a dot while three dots emerge; brightness wave; reform with overshoot
THINK = body_pose(0, "DOT") + [hold(BODY_ID, OP, 0, 0), hold(EYES, OP, 0, 0), hold(DOTS, OP, 0, 100), k(D1, X, 0, -44), k(D3, X, 0, 44)]
anim("thinking_in", 32, False, eyes_shut(0, 4) + to_dot(4, 16) + [hold(EYES, OP, 6, 0), hold(DOTS, OP, 0, 0), hold(DOTS, OP, 16, 100), hold(BODY_ID, OP, 16, 0),
     k(D1, X, 0, 0), k(D1, X, 16, 0), k(D1, X, 28, -44, "cubic", OUT), k(D3, X, 0, 0), k(D3, X, 16, 0), k(D3, X, 28, 44, "cubic", OUT)])
anim("thinking", 72, True, THINK + [hold(D1, OP, 0, 100), k(D1, OP, 0, 100), k(D1, OP, 18, 45), k(D1, OP, 54, 45), k(D1, OP, 72, 100), k(D2, OP, 0, 45), k(D2, OP, 18, 100), k(D2, OP, 36, 45), k(D2, OP, 72, 45),
     k(D3, OP, 0, 45), k(D3, OP, 36, 45), k(D3, OP, 54, 100), k(D3, OP, 72, 45), k(DOTS, Y, 0, 165), k(DOTS, Y, 36, 162), k(DOTS, Y, 72, 165)])
anim("thinking_out", 44, False, THINK + [k(D1, X, 12, 0, "cubic", IN), k(D3, X, 12, 0, "cubic", IN), hold(DOTS, OP, 12, 0), hold(BODY_ID, OP, 12, 100)] + from_dot(12, 30) + eyes_wipe(28, 38))
# speaking: bubble pops in, body bounces per phrase, dots type
SPEAK = [hold(BUBBLE, OP, 0, 100), k(BUBBLE, SX, 0, 100), k(BUBBLE, SY, 0, 100)]
anim("speaking_in", 24, False, [hold(BUBBLE, OP, 0, 100), elastic(BUBBLE, SX, 0, 0, 1.0, 0.5), k(BUBBLE, SX, 16, 100), elastic(BUBBLE, SY, 0, 0, 1.0, 0.5), k(BUBBLE, SY, 16, 100), k(ROOT, Y, 0, 0), k(ROOT, Y, 8, -5), k(ROOT, Y, 16, 0)])
anim("speaking", 90, True, SPEAK + [k(ROOT, Y, 0, 0), k(ROOT, Y, 10, -4), k(ROOT, Y, 20, 0), k(ROOT, Y, 45, 0), k(ROOT, Y, 55, -4), k(ROOT, Y, 65, 0), k(ROOT, Y, 90, 0), k(ROOT, R, 0, 0), k(ROOT, R, 22, 2), k(ROOT, R, 66, -2), k(ROOT, R, 90, 0),
     hold(BD2, OP, 0, 0), hold(BD2, OP, 15, 100), hold(BD2, OP, 60, 0), hold(BD3, OP, 0, 0), hold(BD3, OP, 30, 100), hold(BD3, OP, 60, 0)] + blink(70))
# acknowledgement: one squash and stretch, arc eyes
anim("acknowledgement", 40, False, [k(ROOT, SX, 0, 100), k(ROOT, SX, 6, 112), k(ROOT, SX, 14, 94), k(ROOT, SX, 24, 100), k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 86), k(ROOT, SY, 14, 106), k(ROOT, SY, 24, 100), k(ROOT, Y, 0, 0), k(ROOT, Y, 6, 6), k(ROOT, Y, 14, -4), k(ROOT, Y, 24, 0),
     hold(EYEL, OP, 5, 0), hold(EYER, OP, 5, 0), hold(ARCL, OP, 5, 100), hold(ARCR, OP, 5, 100)] + [x for a in (ARCL, ARCR) for key in (SX, SY) for x in (k(a, key, 5, 40), k(a, key, 12, 100, "cubic", OUT))]
     + [k(ARCR, Y, 5, 0), k(ARCR, Y, 12, -4), k(ARCR, Y, 26, 0), hold(ARCL, OP, 32, 0), hold(ARCR, OP, 32, 0), hold(EYEL, OP, 32, 100), hold(EYER, OP, 32, 100)] + [x for e in (EYEL, EYER) for key in (SX, SY) for x in (k(e, key, 32, 40), k(e, key, 40, 100, "cubic", OUT))])
# concern: tilt, narrow, eyes lean in; loop wobbles
CONCERN = [k(ROOT, R, 0, 9), k(ROOT, SX, 0, 94), k(ROOT, Y, 0, 3), k(EYEL, R, 0, 14), k(EYER, R, 0, -14), k(EYEL, SY, 0, 75), k(EYER, SY, 0, 75)]
anim("concern_in", 24, False, [k(ROOT, R, 14, 9), k(ROOT, SX, 14, 94), k(ROOT, Y, 14, 3), k(EYEL, R, 14, 14), k(EYER, R, 14, -14), k(EYEL, SY, 14, 75), k(EYER, SY, 14, 75)])
anim("concern", 120, True, CONCERN + [k(ROOT, R, 60, 7), k(ROOT, R, 120, 9), k(ROOT, Y, 60, 1), k(ROOT, Y, 120, 3)] + [k(EYEL, SY, 50, 75), k(EYEL, SY, 55, 8), k(EYEL, SY, 61, 75), k(EYER, SY, 50, 75), k(EYER, SY, 55, 8), k(EYER, SY, 61, 75)])
# building: hard hat drops; comet streaks sweep in and orbit the head (two behind, one in front); gentle tumble; eyes squint
def orbit_run(f0, f1, o, extra=0):
    """Linear trim offset over [f0, f1] covering o["revs"] revolutions (plus extra %), keyed per revolution so it wraps cleanly."""
    ks = []; total = o["revs"] * 100 + extra; segs = o["revs"] + (1 if extra else 0); start = o["phase"]
    for i in range(segs):
        fa = f0 + (f1 - f0) * (i * 100) / total; fb = f0 + (f1 - f0) * min((i + 1) * 100, total) / total
        a = start % 100; b = a + min(100, total - i * 100)
        ks += [k(o["trim"], TO_, round(fa), a, "linear"), k(o["trim"], TO_, round(fb), b, "linear")]
        start += 100
    return ks
BUILD = [hold(HAT, OP, 0, 100), k(HAT, Y, 0, -100), hold(OB, OP, 0, 100), hold(OF, OP, 0, 100), k(EYEL, SY, 0, 60), k(EYER, SY, 0, 60), k(ROOT, R, 0, -14)] + [x for o in ORBITS for x in (k(o["trim"], TS_, 0, 0), k(o["trim"], TE_, 0, STREAK))]
anim("building_in", 44, False, eyes_shut(0, 4) + [hold(HAT, OP, 3, 100), elastic(HAT, Y, 3, -160, 0.6, 0.5), k(HAT, Y, 18, -100), k(ROOT, SY, 12, 100), k(ROOT, SY, 17, 93), k(ROOT, SY, 26, 100), hold(OB, OP, 8, 100), hold(OF, OP, 10, 100)]
     + [x for i, o in enumerate(ORBITS) for x in (k(o["trim"], TE_, 8 + i * 3, 0), k(o["trim"], TE_, 30 + i * 3, STREAK, "cubic", OUT))] + [x for o in ORBITS for x in orbit_run(8, 44, o, extra=25)]
     + eyes_open(12, 22, 60) + [k(ROOT, R, 20, 0), k(ROOT, R, 44, -14)])
anim("building", 144, True, BUILD + [k(ROOT, R, 72, 10), k(ROOT, R, 144, -14), k(ROOT, Y, 0, 0), k(ROOT, Y, 36, -3), k(ROOT, Y, 72, 0), k(ROOT, Y, 108, -3), k(ROOT, Y, 144, 0)]
     + [x for o in ORBITS for x in orbit_run(0, 144, {**o, "phase": (o["phase"] + 25) % 100})])
anim("building_out", 22, False, BUILD + [k(HAT, Y, 0, -100, "cubic", IN), k(HAT, Y, 12, -180), hold(HAT, OP, 12, 0)]
     + [x for o in ORBITS for x in (k(o["trim"], TS_, 0, 0, "cubic", IN), k(o["trim"], TS_, 16, STREAK))] + [x for o in ORBITS for x in orbit_run(0, 22, {**o, "revs": 0, "phase": (o["phase"] + 25) % 100}, extra=20)]
     + [hold(OB, OP, 17, 0), hold(OF, OP, 17, 0), k(ROOT, R, 0, -14), k(ROOT, R, 16, 0), k(EYEL, SY, 0, 60), k(EYEL, SY, 14, 100), k(EYER, SY, 0, 60), k(EYER, SY, 14, 100), k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 104), k(ROOT, SY, 16, 100)])
# completion: stamp rises from below, presses with a squash, the check pops, arc eyes; holds
anim("completion", 72, False, [hold(STAMP, OP, 0, 100), k(STAMP, Y, 0, -60, "cubic", OUT), k(STAMP, Y, 12, -110), k(STAMP, Y, 18, -100), k(STAMP, Y, 22, -100, "cubic", IN), k(STAMP, Y, 27, -92), k(STAMP, Y, 34, -102), k(STAMP, Y, 40, -100),
     k(ROOT, SY, 22, 100), k(ROOT, SY, 28, 94), k(ROOT, SY, 34, 104), k(ROOT, SY, 42, 100), k(ROOT, Y, 22, 0), k(ROOT, Y, 28, 4), k(ROOT, Y, 36, 0), hold(CHECK, OP, 28, 100), elastic(CHECK, SX, 28, 0, 1.0, 0.55), k(CHECK, SX, 44, 100), elastic(CHECK, SY, 28, 0, 1.0, 0.55), k(CHECK, SY, 44, 100),
     hold(EYEL, OP, 32, 0), hold(EYER, OP, 32, 0), hold(ARCL, OP, 32, 100), hold(ARCR, OP, 32, 100)] + [x for a in (ARCL, ARCR) for key in (SX, SY) for x in (k(a, key, 32, 40), k(a, key, 40, 100, "cubic", OUT))])
# ship: satchel flies in, look right, wind up, shrink to a dot, trail draws on, dash off right; out brings the dot back and regrows
anim("ship", 78, False, [hold(SATCHEL, OP, 0, 100), k(SATCHEL, X, 0, -140, "cubic", OUT), k(SATCHEL, X, 12, -100), k(EYEL, X, 0, -30), k(EYEL, X, 10, -25), k(EYER, X, 0, 30), k(EYER, X, 10, 35), k(ROOT, R, 6, 0), k(ROOT, R, 18, 10),
     k(ROOT, X, 18, 0), k(ROOT, X, 26, -8)] + eyes_shut(24, 30) + [hold(EYES, OP, 30, 0), hold(SATCHEL, OP, 30, 0)] + body_pose(26, "NEUTRAL", "cubic", IN) + body_pose(34, "PILL", "cubic", IN) + body_pose(42, "DOT", "cubic", OUT) + [
     hold(TRAIL, OP, 40, 100)] + [x for t in TRT for x in (k(t, TE, 40, 0), k(t, TE, 52, 100, "cubic", OUT))] + [k(ROOT, X, 40, -8, "cubic", IN), k(ROOT, X, 50, 24), k(ROOT, X, 70, 260), k(TRAIL, X, 40, 76, "cubic", IN), k(TRAIL, X, 50, 108), k(TRAIL, X, 70, 344), k(ROOT, OP, 64, 100), k(ROOT, OP, 72, 0), hold(TRAIL, OP, 66, 0)])
anim("ship_out", 50, False, [k(ROOT, X, 0, 260, "cubic", OUT), k(ROOT, X, 18, 0), hold(ROOT, OP, 0, 100), hold(EYES, OP, 0, 0), hold(TRAIL, OP, 0, 0)] + body_pose(0, "DOT") + from_dot(18, 36) + eyes_wipe(34, 44))
# reconnecting: squeeze into an exclamation with a dot, wobble, relax; repeats every 4 s
anim("reconnecting", 240, True, eyes_shut(0, 4) + body_pose(4, "NEUTRAL", "cubic", IN) + body_pose(9, "DROP", "cubic", IN) + body_pose(16, "EXCLAIM", "cubic", OUT) + [k(EYES, SX, 4, 100), k(EYES, SX, 16, 55), k(ROOT, Y, 5, 0), k(ROOT, Y, 14, -12), hold(EXCL, OP, 8, 100), elastic(EXCL, SX, 8, 0, 1.0, 0.5), k(EXCL, SX, 18, 100), elastic(EXCL, SY, 8, 0, 1.0, 0.5), k(EXCL, SY, 18, 100), k(EXCL, Y, 5, 261), k(EXCL, Y, 14, 272), k(EXCL, Y, 122, 272), k(EXCL, Y, 134, 261)]
     + eyes_open(10, 20, 70) + [k(ROOT, R, 14, 0), k(ROOT, R, 40, 5), k(ROOT, R, 70, -5), k(ROOT, R, 100, 5), k(ROOT, R, 120, 0)] + eyes_shut(116, 122)
     + body_pose(122, "EXCLAIM", "cubic", IN) + body_pose(128, "DROP", "cubic", OUT) + body_pose(138, "NEUTRAL") + [k(EYES, SX, 122, 55), k(EYES, SX, 138, 100), k(ROOT, SX, 134, 100), k(ROOT, SX, 141, 106), k(ROOT, SX, 148, 100), k(ROOT, SY, 134, 100), k(ROOT, SY, 141, 106), k(ROOT, SY, 148, 100), k(ROOT, Y, 122, -12), k(ROOT, Y, 134, 0), hold(EXCL, OP, 124, 0)] + eyes_open(134, 144) + blink(200))
# paused: slits, sinks; loop breathes slowly with a twitch
PAUSE = [k(EYEL, SY, 0, 22), k(EYER, SY, 0, 22), k(ROOT, Y, 0, 8), k(ROOT, SY, 0, 97), k(ROOT, SX, 0, 102)]
anim("paused_in", 20, False, [k(EYEL, SY, 14, 22), k(EYER, SY, 14, 22), k(ROOT, Y, 14, 8), k(ROOT, SY, 14, 97), k(ROOT, SX, 14, 102)])
anim("paused", 240, True, PAUSE + [k(ROOT, Y, 120, 10), k(ROOT, Y, 240, 8), k(ROOT, SY, 120, 98.5), k(ROOT, SY, 240, 97), k(EYEL, SY, 140, 22), k(EYEL, SY, 146, 14), k(EYEL, SY, 152, 22), k(EYER, SY, 140, 22), k(EYER, SY, 146, 14), k(EYER, SY, 152, 22)])
# sleep: eyes close, body shrinks to a dot, particles scatter; loop bobs; out regrows
SLEEP = body_pose(0, "DOT") + [hold(EYES, OP, 0, 0), hold(PARTS, OP, 0, 100)]
anim("sleep_in", 36, False, [k(EYEL, SY, 0, 100), k(EYEL, SY, 4, 22), k(EYEL, SY, 8, 8), k(EYER, SY, 0, 100), k(EYER, SY, 4, 22), k(EYER, SY, 8, 8), hold(EYES, OP, 9, 0)] + to_dot(6, 18) + [
     hold(PARTS, OP, 12, 100), k(P1, X, 12, 0), k(P1, X, 26, 26, "cubic", OUT), k(P1, Y, 12, 0), k(P1, Y, 26, -6, "cubic", OUT), k(P2, X, 12, 0), k(P2, X, 26, 40, "cubic", OUT), k(P2, Y, 12, 0), k(P2, Y, 26, -24, "cubic", OUT)])
anim("sleep", 180, True, SLEEP + [k(ROOT, Y, 0, 0), k(ROOT, Y, 90, -4), k(ROOT, Y, 180, 0), k(P1, X, 0, 26), k(P1, X, 90, 30), k(P1, X, 180, 26), k(P1, Y, 0, -6), k(P1, Y, 90, -10), k(P1, Y, 180, -6),
     k(P2, X, 0, 40), k(P2, X, 90, 36), k(P2, X, 180, 40), k(P2, Y, 0, -24), k(P2, Y, 90, -28), k(P2, Y, 180, -24), k(P2, OP, 0, 100), k(P2, OP, 45, 40), k(P2, OP, 90, 100), k(P2, OP, 135, 40), k(P2, OP, 180, 100)])
anim("sleep_out", 44, False, SLEEP + [k(P1, X, 0, 26), k(P1, X, 12, 0, "cubic", IN), k(P1, Y, 0, -6), k(P1, Y, 12, 0, "cubic", IN), k(P2, X, 0, 40), k(P2, X, 12, 0, "cubic", IN), k(P2, Y, 0, -24), k(P2, Y, 12, 0, "cubic", IN), hold(PARTS, OP, 12, 0)]
     + from_dot(10, 28) + eyes_wipe(26, 36))
# investigating: deerstalker drops, lands with a squash, magnifier rises, one eye raised; loop searches
INVEST = [hold(DEER, OP, 0, 100), k(DEER, Y, 0, -100), hold(MAG, OP, 0, 100), k(MAG, Y, 0, -100), k(EYER, Y, 0, -4)]
anim("investigating_in", 48, False, eyes_shut(0, 4) + [hold(DEER, OP, 3, 100), elastic(DEER, Y, 3, -160, 0.6, 0.5), k(DEER, Y, 18, -100), k(ROOT, SY, 12, 100), k(ROOT, SY, 17, 93), k(ROOT, SY, 26, 100)] + eyes_open(12, 22)
     + [hold(MAG, OP, 22, 100), k(MAG, Y, 22, -60, "cubic", OUT), k(MAG, Y, 36, -104), k(MAG, Y, 42, -100), k(EYER, Y, 36, 0), k(EYER, Y, 44, -4)])
anim("investigating", 150, True, INVEST + [k(MAG, R, 0, 0), k(MAG, R, 40, 4), k(MAG, R, 80, -3), k(MAG, R, 150, 0), k(MAG, Y, 40, -102), k(MAG, Y, 80, -100),
     k(EYEL, X, 0, -30), k(EYEL, X, 30, -25), k(EYEL, X, 60, -25), k(EYEL, X, 100, -34), k(EYEL, X, 130, -30), k(EYER, X, 0, 30), k(EYER, X, 30, 35), k(EYER, X, 60, 35), k(EYER, X, 100, 26), k(EYER, X, 130, 30)] + breathe(150, 2))
# exits: accessories leave the way they came (lift off / draw off / shrink), with a small recovery squash
anim("listening_out", 18, False, LISTEN + [k(HEADSET, Y, 0, 0, "cubic", IN), k(HEADSET, Y, 12, -70), hold(HEADSET, OP, 12, 0), k(ROOT, R, 0, -7), k(ROOT, R, 14, 0), k(EYEL, X, 0, -26), k(EYEL, X, 14, -30), k(EYER, X, 0, 34), k(EYER, X, 14, 30),
     k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 104), k(ROOT, SY, 16, 100)])
anim("investigating_out", 20, False, INVEST + [k(DEER, Y, 0, -100, "cubic", IN), k(DEER, Y, 12, -170), hold(DEER, OP, 12, 0), k(MAG, Y, 0, -100, "cubic", IN), k(MAG, Y, 12, -50), hold(MAG, OP, 12, 0), k(EYER, Y, 0, -4), k(EYER, Y, 12, 0),
     k(ROOT, SY, 0, 100), k(ROOT, SY, 6, 104), k(ROOT, SY, 16, 100)])
anim("speaking_out", 14, False, SPEAK + [k(BUBBLE, SX, 0, 100, "cubic", IN), k(BUBBLE, SX, 10, 0), k(BUBBLE, SY, 0, 100, "cubic", IN), k(BUBBLE, SY, 10, 0), hold(BUBBLE, OP, 11, 0)])
# ---------------------------------------------------------------- write animations
anims = call("animation_editor", {"command": "listLinearAnimations"})["linearAnimations"]
existing = {a["name"]: a["id"] for a in anims}
if "idle" not in existing:
    call("animation_editor", {"command": "renameAnimations", "data": {"renameAnimations": {"animations": [{"animationId": anims[0]["id"], "name": "idle"}]}}}); existing["idle"] = anims[0]["id"]
todo = [n for n in A if n not in existing]
if todo:
    made = call("animation_editor", {"command": "createLinearAnimations", "data": {"createLinearAnimations": {"linearAnimations": [{"name": n, "duration": DUR[n] / 60} for n in todo]}}})["animations"]
    existing.update({a["name"]: a["id"] for a in made})
ANIM = {n: existing[n] for n in A}
set_props({ANIM[n]: {"59": 1 if n in LOOP else 0, "57": DUR[n]} for n in A})
for name in A:
    keys = {}
    for kf in neutral() + A[name]: keys[(kf["objectId"], kf["propertyKey"], kf["frame"])] = kf
    ks = list(keys.values())
    for i in range(0, len(ks), 60): ok(call("animation_editor", {"command": "modifyKeyFrames", "data": {"modifyKeyFrames": {"animationId": ANIM[name], "add": ks[i:i + 60]}}}), f"keys {name}")
print("animations keyed:", len(A))
# ---------------------------------------------------------------- view model (14 states)
enums = call("viewmodel_editor", {"command": "listDataEnums"}).get("dataEnums", [])
if not any(e["name"] == "CharacterState" for e in enums):
    call("viewmodel_editor", {"command": "createDataEnums", "data": {"createDataEnums": {"dataEnums": [{"name": "CharacterState", "values": STATES}]}}})
vms = [v for v in call("viewmodel_editor", {"command": "listViewModels"})["viewModels"] if v["name"] == "AgentCharacterModel"]
vm = vms[0] if vms else call("viewmodel_editor", {"command": "createViewModels", "data": {"createViewModels": {"viewModels": [{"name": "AgentCharacterModel", "viewModelProperties": [
    {"name": "state", "propertyType": "enum", "enumName": "CharacterState"}, {"name": "gazeX", "propertyType": "number"}, {"name": "gazeY", "propertyType": "number"}, {"name": "speechEnergy", "propertyType": "number"}, {"name": "motionScale", "propertyType": "number"}]}]}}})["viewModels"][0]
P = {p["name"]: p["id"] for p in vm["viewModelProperties"]}
ok(call("viewmodel_editor", {"command": "bindViewModelToArtboard", "data": {"bindViewModelToArtboard": {"artboardId": I["ab"], "viewModelId": vm["id"]}}}), "bind vm")
conv = call("viewmodel_editor", {"command": "createConverters", "data": {"createConverters": {"converters": [{"converterType": "formula", "name": "gazeToX", "formula": "{Input} * 12"}, {"converterType": "formula", "name": "gazeToY", "formula": f"{{Input}} * 12 {'+' if EY >= 0 else '-'} {abs(EY)}"}, {"converterType": "formula", "name": "speechToScaleRaw", "formula": "1 + {Input} * 0.12"}]}}})["converters"]
CV = {c["name"]: c["id"] for c in conv}
ok(call("viewmodel_editor", {"command": "databind", "data": {"databind": {"viewModelId": vm["id"], "bindings": [
    {"objectId": EYES, "propertyKey": 13, "viewModelPropertyId": P["gazeX"], "converterId": CV["gazeToX"]}, {"objectId": EYES, "propertyKey": 14, "viewModelPropertyId": P["gazeY"], "converterId": CV["gazeToY"]},
    {"objectId": I["bd2"], "propertyKey": 16, "viewModelPropertyId": P["speechEnergy"], "converterId": CV["speechToScaleRaw"]}, {"objectId": I["bd2"], "propertyKey": 17, "viewModelPropertyId": P["speechEnergy"], "converterId": CV["speechToScaleRaw"]}]}}}), "databind")
# ---------------------------------------------------------------- state machine
sm = call("animation_editor", {"command": "listStateMachines"})["stateMachines"][0]
call("rename_objects", {"renames": [{"id": sm["id"], "name": "AgentCharacterMachine"}]})
q = call("animation_editor", {"command": "queryStateMachine", "data": {"queryStateMachine": {"stateMachineId": sm["id"]}}})["layers"][0]
layer = q["layerId"]; idle_state = [s for s in q["states"] if s["type"] == "animation"][0]["id"]
state_names = [n for n in A if n != "idle"] + ["pointer"]
st = call("animation_editor", {"command": "createStates", "data": {"createStates": {"layerId": layer, "states": [{"name": n, "linearAnimationName": "idle" if n == "pointer" else n, "x": 400 + 220 * (i // 8), "y": -400 + 100 * (i % 8)} for i, n in enumerate(state_names)]}}})["states"]
SID = {s["name"]: s["id"] for s in st if s["type"] == "animation" and s["name"] in state_names}; SID["idle"] = idle_state
# transition table: (from, to, kind) kind = ("enum", state) | ("not", state) | ("exit",)
PHASED = {"listening", "thinking", "speaking", "concern", "building", "paused", "sleep", "investigating"}
OUTS = {"thinking": "thinking_out", "sleep": "sleep_out", "ship": "ship_out", "listening": "listening_out", "building": "building_out", "investigating": "investigating_out", "speaking": "speaking_out"}
T = []
for s in STATES:
    if s == "idle": continue
    if s in PHASED:
        T += [("idle", s + "_in", ("enum", s)), (s + "_in", s, ("exit",))]
        leave = OUTS.get(s, "idle")
        T += [(s + "_in", leave, ("not", s)), (s, leave, ("not", s))]
    else:
        T += [("idle", s, ("enum", s)), (s, OUTS.get(s, "idle"), ("not", s))]
for o in OUTS.values(): T.append((o, "idle", ("exit",)))
by_from = {}
for f, t, kind in T: by_from.setdefault(f, []).append((t, kind))
tr = call("animation_editor", {"command": "createTransitions", "data": {"createTransitions": {"states": [{"id": SID[f], "transitions": [{"to": SID[t]} for t, _ in lst]} for f, lst in by_from.items()]}}})["transitions"]
# map (from, to) names to the created transition ids; the renamed default state reports an empty name
def nm(x): return x or "idle"
tid = {}
for f, t, kind in T:
    tid[(f, t)] = [x["id"] for x in tr if nm(x["from"]) == f and nm(x["to"]) == t][0]
conds = []; durations = {}; exits = []
for f, t, kind in T:
    i = tid[(f, t)]
    if kind[0] == "enum": conds.append({"id": i, "conditions": [{"leftComparator": {"viewModelPropertyId": P["state"]}, "comparationOperation": "equal", "rightComparator": {"value": kind[1], "valueType": "constantValueType"}}]}); durations[i] = 100
    elif kind[0] == "not": conds.append({"id": i, "conditions": [{"leftComparator": {"viewModelPropertyId": P["state"]}, "comparationOperation": "notEqual", "rightComparator": {"value": kind[1], "valueType": "constantValueType"}}]}); durations[i] = 160
    else: exits.append(i); durations[i] = 0
ok(call("animation_editor", {"command": "createConditions", "data": {"createConditions": {"transitions": conds}}}), "conditions")
set_props({i: {"158": d} for i, d in durations.items()})
# transition flags: 1 disabled, 2 duration is %, 4 enable exit time, 8 exit time is %, 16 pause on exit
set_props({i: {"152": 12, "160": 100} for i in exits})
json.dump({"exits": exits, "tid": {f"{a}->{b}": v for (a, b), v in tid.items()}}, open(f"sm_{NAME}.json", "w"))
print("state machine built; exit transitions:", len(exits))
if "--no-export" not in sys.argv:
    e = call("export_file", {"format": "riv", "destination": EXPORT_DIR, "inline_base64": True})
    if isinstance(e, str): e = json.loads(e)
    out = f"{EXPORT_DIR}/utrace-agent-{NAME}.riv"; open(out, "wb").write(base64.b64decode(e["data"])); print("exported", out, os.path.getsize(out), "bytes")
