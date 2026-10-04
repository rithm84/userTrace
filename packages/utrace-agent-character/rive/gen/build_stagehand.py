"""Build the scroll-scrubbed cord grip: python3 build_stagehand.py <mint|black>.

Run in an initialized MCP session with a dedicated ACTIVE Rive file. The page
draws the cord and scrubs the non-looping 72-frame pull timeline at 60 fps.
StagehandMachine is renamed but its default contents are left untouched.
"""
from __future__ import annotations

import argparse

from landing_common import (
    COLOURS, EASE, EYE, GEN, INK, OP, R, SX, SY, VX, VY, WHITE, X, Y,
    Animation, Keyframe, api, body, converters, export_rig, fill, frame, group,
    new_artboard, oid, one, parametric, rows, shapes, stroke, view_model,
    write_animations,
)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("colour", choices=("mint", "black"))
NAME: str = parser.parse_args().colour
BODY, SHAPE, EY, _ = COLOURS[NAME]
RIM = WHITE if NAME == "black" else INK
ARTBOARD = "Stagehand" + NAME.capitalize()
CACHE = GEN / f".stagehand-{NAME}-bindings.json"

# ---------------------------------------------------------------- rig
AB, SAVED = new_artboard(ARTBOARD, CACHE)
ROOT = group("Root", AB, 150, 176, 100)
BODY_ID, VERTS, MORPHS = body(ROOT, SHAPE, BODY)
EYES = group("Eyes", ROOT, 0, EY, 100)
eyes = shapes(EYES, [("EyeL", -30, 0, [fill(RIM)], EYE), ("EyeR", 30, 0, [fill(RIM)], EYE)])
EYEL, EYER = eyes["EyeL"], eyes["EyeR"]
HANDS = group("Hands", ROOT, 0, 0, 100)
hands = parametric(HANDS, [
    ("HandTop", "ellipse", 34, 34, 92, EY - 18, [fill(BODY), stroke(RIM, 3.5)], {}),
    ("HandBottom", "ellipse", 34, 34, 92, EY + 14, [fill(BODY), stroke(RIM, 3.5)], {}),
])
HT, HB = hands["HandTop"], hands["HandBottom"]
api("reorder_objects", {"operations": [{"objectId": HANDS, "order": "sendToFront"}]})


def k(obj: str, prop: int, f: int, value: float) -> Keyframe:
    offset = 150 if obj == ROOT and prop == X else 176 if obj == ROOT and prop == Y else 0
    return frame(obj, prop, f, value + offset, "cubic", EASE)


def track(obj: str, prop: int, values: tuple[tuple[int, float], ...]) -> list[Keyframe]:
    return [k(obj, prop, f, value) for f, value in values]


# Every authored property has explicit 0 and 72 keys. Bound Eyes.X/Y are not
# timeline properties; all eye expressions are local to EyeL/EyeR instead.
keys: list[Keyframe] = []
for obj in (ROOT, BODY_ID, EYES, HANDS, EYEL, EYER, HT, HB):
    for prop, value in ((OP, 100), (SX, 100), (SY, 100), (R, 0)):
        keys += track(obj, prop, ((0, value), (72, value)))
for obj, x, y in ((ROOT, 0, 0), (BODY_ID, 0, 0), (HANDS, 0, 0),
                  (EYEL, -30, 0), (EYER, 30, 0), (HT, 92, EY - 18), (HB, 92, EY + 14)):
    keys += track(obj, X, ((0, x), (72, x))) + track(obj, Y, ((0, y), (72, y)))
for vertex, (x, y) in zip(VERTS, MORPHS["NEUTRAL"], strict=True):
    keys += track(vertex, VX, ((0, x), (72, x))) + track(vertex, VY, ((0, y), (72, y)))

for hand, rest_y in ((HT, EY - 18), (HB, EY + 14)):
    keys += track(hand, Y, ((0, rest_y), (8, rest_y), (48, rest_y + 56), (60, rest_y + 56), (72, rest_y + 56)))
    keys += track(hand, X, ((0, 92), (8, 92), (48, 92), (60, 92), (72, 100)))
    for prop in (SX, SY):
        keys += track(hand, prop, ((0, 100), (8, 100), (48, 100), (60, 100), (66, 112), (72, 100)))

# The pull itself is monotonic. One explicit overshoot after 48 replaces an
# elastic interpolator, so both scroll directions evaluate the same cubic path.
keys += track(ROOT, R, ((0, 0), (8, 0), (48, -4), (54, -1), (60, -2), (72, 0)))
keys += track(ROOT, SY, ((0, 100), (8, 100), (48, 94), (54, 102), (60, 100), (72, 100)))
keys += track(ROOT, SX, ((0, 100), (8, 100), (48, 103), (54, 99), (60, 100), (72, 100)))
for eye, x in ((EYEL, -30), (EYER, 30)):
    keys += track(eye, X, ((0, x), (8, x + 4), (48, x + 4), (60, x + 4), (72, x)))
    keys += track(eye, Y, ((0, 0), (8, -5), (48, -5), (60, -5), (72, 0)))
    keys += track(eye, SY, ((0, 100), (8, 100), (48, 55), (60, 100), (72, 100)))
unique = {(kf["objectId"], kf["propertyKey"], kf["frame"]): kf for kf in keys}
properties = {(obj, prop) for obj, prop, _ in unique}
for obj, prop in properties:
    if (obj, prop, 0) not in unique or (obj, prop, 72) not in unique:
        raise ValueError(f"pull: property {obj}/{prop} needs both scrub endpoints")
A: dict[str, Animation] = {"pull": (72, False, list(unique.values()))}
write_animations(A, [])

# ---------------------------------------------------------------- gaze binding
VM, P = view_model(AB, "StagehandModel", [
    {"name": "gazeX", "propertyType": "number"}, {"name": "gazeY", "propertyType": "number"},
], CACHE, SAVED)
CV = converters(AB, [
    {"converterType": "formula", "name": f"stagehandGazeToX{NAME}", "formula": "{Input} * 10"},
    {"converterType": "formula", "name": f"stagehandGazeToY{NAME}", "formula": f"{{Input}} * 8 + {EY}"},
], CACHE, SAVED)
api("viewmodel_editor", {"command": "databind", "data": {"databind": {
    "viewModelId": VM, "bindings": [
        {"objectId": EYES, "propertyKey": X, "viewModelPropertyId": P["gazeX"], "converterId": CV[f"stagehandGazeToX{NAME}"]},
        {"objectId": EYES, "propertyKey": Y, "viewModelPropertyId": P["gazeY"], "converterId": CV[f"stagehandGazeToY{NAME}"]},
    ]}}})
sm = one(rows("animation_editor", {"command": "listStateMachines"}, "stateMachines"), "default machine")
api("rename_objects", {"renames": [{"id": oid(sm), "name": "StagehandMachine"}]})
print("built", ARTBOARD, "— pull: 72 frames, non-looping, cubic keys; no added machine states")
export_rig(f"utrace-stagehand-{NAME}.riv")
