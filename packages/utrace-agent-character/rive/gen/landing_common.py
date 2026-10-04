"""Shared Rive 0.6 call shapes for the landing builders; importing does no editor work."""
from __future__ import annotations

import base64
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NotRequired, TypedDict, cast
from urllib.error import HTTPError

import morph
from mcp2 import call as mcp_call

Json = None | bool | int | float | str | list["Json"] | dict[str, "Json"]
Record = dict[str, Json]
Point = tuple[float, float]
Command = tuple[str | float, ...]
ShapeSpec = tuple[str, float, float, list[Record], list[Command]]
PrimitiveSpec = tuple[str, str, float, float, float, float, list[Record], Record]
Keyframe = TypedDict("Keyframe", {
    "objectId": str, "propertyKey": int, "frame": int, "value": float,
    "interpolationType": str, "cubicParams": NotRequired[dict[str, float]],
    "elasticParams": NotRequired[dict[str, float]],
})
Animation = tuple[int, bool, list[Keyframe]]
GEN = Path(__file__).resolve().parent
EXPORT_DIR = GEN.parents[3] / "apps/web/public/landing"
X, Y, R, SX, SY, OP, VX, VY = 13, 14, 15, 16, 17, 18, 24, 25
INK, WHITE = "#FF111111", "#FFFFFFFF"
COLOURS: dict[str, tuple[str, str, int, int]] = {
    "coral": ("#FFFCADA3", "circle", 5, 12),
    "black": ("#FF111111", "square", 5, 18),
    "mint": ("#FF97D6A4", "hexagon", 5, 3),
}
EASE = {"x1": 0.42, "y1": 0.0, "x2": 0.58, "y2": 1.0}
OUT = {"x1": 0.16, "y1": 0.84, "x2": 0.3, "y2": 1.0}
IN = {"x1": 0.55, "y1": 0.0, "x2": 0.8, "y2": 0.25}
EYE: list[Command] = [
    ("M", 20.79, 0), ("C", 20.79, 21.168, 12.474, 35.28, 0, 35.28),
    ("C", -12.474, 35.28, -20.79, 21.168, -20.79, 0),
    ("C", -20.79, -21.168, -12.474, -35.28, 0, -35.28),
    ("C", 12.474, -35.28, 20.79, -21.168, 20.79, 0), ("Z",),
]
ARC: list[Command] = [("M", -22.05, 8.4), ("C", -14.7, -8.9, 14.7, -8.9, 22.05, 8.4)]


def record(value: object, where: str) -> Record:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ValueError(f"{where}: expected an object, received {value!r}")
    return cast(Record, value)


def records(value: object, where: str) -> list[Record]:
    if not isinstance(value, list):
        raise ValueError(f"{where}: expected an array, received {value!r}")
    return [record(item, where) for item in value]


def string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where}: expected a string, received {value!r}")
    return value


def number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where}: expected a finite number, received {value!r}")
    return float(value)


def one(items: Sequence[Record], where: str) -> Record:
    if len(items) != 1:
        raise ValueError(f"{where}: expected exactly one object, received {items!r}")
    return items[0]


def oid(item: Record) -> str:
    return string(item["id"], "object id")


def api(name: str, arguments: Mapping[str, object]) -> Record:
    """Require explicit success; never retry mutations that could have already applied."""
    # SVG uploads are large; their full data URI is not useful in an error.
    params = {k: "<SVG data URI>" if k == "file" else v for k, v in arguments.items()}
    try:
        response: object = mcp_call(name, arguments)
    except HTTPError as error:
        raise RuntimeError(f"Rive {name} HTTP {error.code}; params={params!r}; "
                           f"response={error.read().decode('utf-8', errors='replace')}") from error
    except (OSError, RuntimeError, ValueError, KeyError, IndexError) as error:
        raise RuntimeError(f"Rive {name} failed; params={params!r}; cause={error}") from error
    if isinstance(response, str):
        response = json.loads(response)
    result = record(response, name)
    if result.get("success") is not True:
        raise RuntimeError(f"Rive {name} failed; params={params!r}; response={result!r}")
    return result


def rows(name: str, arguments: Mapping[str, object], field: str) -> list[Record]:
    return records(api(name, arguments)[field], f"{name}.{field}")


def set_props(values: Mapping[str, Mapping[str, float | bool]]) -> None:
    api("set_property_values", {"propertyValues": values})


def children(obj: str) -> list[Record]:
    return rows("query_objects", {"objectIds": [obj], "depth": 1}, "objects")


def has_type(item: Record, kind: str) -> bool:
    types = item["types"]
    if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
        raise ValueError(f"Invalid object types: {item!r}")
    return any(kind in str(t) for t in types)


def fill(colour: str) -> Record:
    return {"paintType": "fill", "color": colour}


def stroke(colour: str, width: float) -> Record:
    return {"paintType": "stroke", "color": colour, "width": width}


def cmds(points: Sequence[Command]) -> list[Record]:
    out: list[Record] = []
    for p in points:
        if p[0] == "M": out.append({"commandType": "moveTo", "x": p[1], "y": p[2]})
        elif p[0] == "L": out.append({"commandType": "lineTo", "x": p[1], "y": p[2]})
        elif p[0] == "C":
            out.append({"commandType": "cubicTo", "control1X": p[1], "control1Y": p[2],
                        "control2X": p[3], "control2Y": p[4], "endX": p[5], "endY": p[6]})
        elif p[0] == "Z": out.append({"commandType": "close"})
        else: raise ValueError(f"Unsupported path command: {p!r}")
    return out


def group(name: str, parent: str, x: float, y: float, op: float) -> str:
    g = oid(record(api("group_editor", {"name": name, "parentId": parent})["group"], name))
    set_props({g: {"13": x, "14": y, "18": op}})
    return g


def shapes(parent: str, specs: Sequence[ShapeSpec]) -> dict[str, str]:
    api("path_editor", {"command": "createShapes", "data": {"createShapes": {"shapes": [
        {"name": n, "parentId": parent, "x": x, "y": y, "paints": p,
         "paths": [{"name": "p", "commands": cmds(pts)}]} for n, x, y, p, pts in specs]}}})
    out: dict[str, str] = {}
    for n, _, _, _, pts in specs:
        found = rows("find_objects", {"name": n, "type": "shape"}, "objects")
        out[n] = oid(one([o for o in found if o["name"] == n], f"shape {n}"))
        if pts[-1][0] != "Z":
            paths = [o for o in children(out[n]) if has_type(o, "PointsPath")]
            set_props({oid(one(paths, f"open path {n}")): {"32": False}})
    return out


def parametric(parent: str, specs: Sequence[PrimitiveSpec]) -> dict[str, str]:
    made = rows("path_editor", {"command": "createParametricShapes", "data": {
        "createParametricShapes": {"shapes": [
            {"primitive": pr, "name": n, "width": w, "height": h, "x": x, "y": y,
             "parentId": parent, "paints": p, **ex} for n, pr, w, h, x, y, p, ex in specs]}}}, "shapes")
    return {string(s["name"], "shape name"): oid(s) for s in made}


def round_stroke(shape: str) -> None:
    paints = [o for o in children(shape) if has_type(o, "Stroke")]
    if not paints: raise ValueError(f"Shape {shape} has no stroke to round")
    set_props({oid(o): {"48": 1, "49": 1} for o in paints})


def body(root: str, silhouette: str, colour: str) -> tuple[str, list[str], dict[str, list[Point]]]:
    poses: dict[str, list[Point]] = morph.dense(silhouette, 120)
    dp = poses["NEUTRAL"]
    points: list[Command] = [("M", *dp[0])] + [("L", x, y) for x, y in dp[1:]] + [("Z",)]
    sid = shapes(root, [("Body", 0, 0, [fill(colour)], points)])["Body"]
    path = one([o for o in children(sid) if has_type(o, "PointsPath")], "body path")
    verts = [oid(v) for v in children(oid(path)) if has_type(v, "Vertex")]
    if len(verts) != len(dp):
        raise ValueError(f"Body vertex count: expected {len(dp)}, received {len(verts)}")
    sharp = {(round(x), round(y)) for x, y in morph.CORNERS.get(silhouette, [])}
    set_props({v: {"26": 0 if (round(x), round(y)) in sharp else 8}
               for v, (x, y) in zip(verts, dp, strict=True)})
    return sid, verts, poses


def frame(obj: str, key: int, at: int, value: float, interpolation: str,
          params: dict[str, float]) -> Keyframe:
    result: Keyframe = {"objectId": obj, "propertyKey": key, "frame": at,
                        "value": value, "interpolationType": interpolation}
    if interpolation == "cubic": result["cubicParams"] = params
    elif interpolation == "elastic": result["elasticParams"] = params
    elif interpolation not in ("hold", "linear"):
        raise ValueError(f"Unsupported interpolation: {interpolation}")
    return result


def write_animations(animations: Mapping[str, Animation], neutral: Sequence[Keyframe]) -> None:
    existing = rows("animation_editor", {"command": "listLinearAnimations"}, "linearAnimations")
    first_name = next(iter(animations))
    first = oid(one(existing, "new artboard's default animation"))
    api("animation_editor", {"command": "renameAnimations", "data": {"renameAnimations": {
        "animations": [{"animationId": first, "name": first_name}]}}})
    ids = {first_name: first}
    todo = [n for n in animations if n != first_name]
    if todo:
        made = rows("animation_editor", {"command": "createLinearAnimations", "data": {
            "createLinearAnimations": {"linearAnimations": [
                {"name": n, "duration": animations[n][0] / 60} for n in todo]}}}, "animations")
        ids.update({string(a["name"], "animation name"): oid(a) for a in made})
    set_props({ids[n]: {"59": 1 if loop else 0, "57": duration}
               for n, (duration, loop, _) in animations.items()})
    for name, (duration, _, keys) in animations.items():
        unique = {(k["objectId"], k["propertyKey"], k["frame"]): k for k in [*neutral, *keys]}
        ks = list(unique.values())
        if any(k["frame"] < 0 or k["frame"] > duration for k in ks):
            raise ValueError(f"{name}: key outside 0..{duration}")
        for i in range(0, len(ks), 60):
            api("animation_editor", {"command": "modifyKeyFrames", "data": {
                "modifyKeyFrames": {"animationId": ids[name], "add": ks[i:i + 60]}}})
    print("animations keyed:", len(animations))


def cache_write(path: Path, cache: Record) -> None:
    """Keep binding ownership through artboard replacement, including interrupted builds."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache, indent=2) + "\n")
    temporary.replace(path)


def new_artboard(name: str, cache_path: Path) -> tuple[str, Record]:
    boards = rows("list_artboards", {}, "artboards")
    # export_file exports only the first board; never delete a different user's board.
    if any(b["name"] != name for b in boards):
        raise RuntimeError(f"Open a dedicated Rive file for {name}; export_file only exports its first artboard. "
                           f"Other artboards: {[b['name'] for b in boards if b['name'] != name]}")
    old_ids = [oid(b) for b in boards]
    cache = record(json.loads(cache_path.read_text()), str(cache_path)) if cache_path.exists() else {}
    selected = [record(cache[i], f"bindings for {i}") for i in old_ids if i in cache]
    if not old_ids and "pending" in cache:
        raise RuntimeError(f"An interrupted artboard replacement is recorded in {cache_path}. "
                           "Restore/open its previous artboard before rebuilding; file identity is ambiguous.")
    saved = one(selected, "cached file bindings") if selected else {}
    if old_ids:
        cache["pending"] = cast(Json, old_ids)
        cache_write(cache_path, cache)
        api("delete_objects", {"objectIds": old_ids})
    made = rows("open_file_editor", {"command": "createArtboard", "data": {
        "createArtboard": [{"name": name, "width": 300, "height": 300}]}}, "artboards")
    ab = oid(one(made, "created artboard"))
    cache = {key: value for key, value in cache.items() if key not in old_ids}
    cache[ab] = saved
    cache.pop("pending", None)
    cache_write(cache_path, cache)
    api("open_file_editor", {"command": "focusArtboard", "data": {
        "focusArtboard": {"artboardId": ab, "fitToViewport": True}}})
    for o in rows("get_artboard_hierarchy", {"artboardId": ab, "depth": 1}, "objects"):
        if has_type(o, "Fill"): api("delete_objects", {"objectIds": [oid(o)]})
    return ab, saved


def save_binding(cache_path: Path, ab: str, key: str, value: Json) -> None:
    cache = record(json.loads(cache_path.read_text()), str(cache_path))
    saved = record(cache[ab], f"bindings for {ab}")
    cache_write(cache_path, {**cache, ab: {**saved, key: value}})


def view_model(ab: str, name: str, properties: Sequence[Record], cache_path: Path,
               saved: Record) -> tuple[str, dict[str, str]]:
    reported = rows("viewmodel_editor", {"command": "listViewModels"}, "viewModels")
    matching = [v for v in reported if v["name"] == name]
    if matching: vm = one(matching, name)
    elif "vm" in saved: vm = record(saved["vm"], name)
    else:
        vm = one(rows("viewmodel_editor", {"command": "createViewModels", "data": {
            "createViewModels": {"viewModels": [{"name": name,
                "viewModelProperties": properties}]}}}, "viewModels"), name)
        save_binding(cache_path, ab, "vm", vm)
    props = {string(p["name"], "property name"): oid(p)
             for p in records(vm["viewModelProperties"], name)}
    if not {p["name"] for p in properties}.issubset(props):
        raise ValueError(f"{name}: incompatible existing properties {list(props)}")
    save_binding(cache_path, ab, "vm", vm)
    api("viewmodel_editor", {"command": "bindViewModelToArtboard", "data": {
        "bindViewModelToArtboard": {"artboardId": ab, "viewModelId": oid(vm)}}})
    return oid(vm), props


def converters(ab: str, specs: Sequence[Record], cache_path: Path, saved: Record) -> dict[str, str]:
    if "converters" in saved: made = records(saved["converters"], "cached converters")
    else:
        made = rows("viewmodel_editor", {"command": "createConverters", "data": {
            "createConverters": {"converters": specs}}}, "converters")
        save_binding(cache_path, ab, "converters", cast(Json, made))
    ids = {string(c["name"], "converter name"): oid(c) for c in made}
    if set(ids) != {s["name"] for s in specs}:
        raise ValueError(f"Incompatible cached converters in {cache_path}: {list(ids)}")
    return ids


def export_rig(filename: str) -> None:
    result = api("export_file", {"format": "riv", "destination": str(EXPORT_DIR), "inline_base64": True})
    data = base64.b64decode(string(result["data"], "export data"), validate=True)
    if not data: raise ValueError("Rive export returned empty data")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / filename
    out.write_bytes(data)
    print("exported", out, len(data), "bytes")
