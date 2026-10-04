"""Body morph geometry: every body is a closed path of cubic vertices with detached handles, and the morph targets
(drop, dot, pill, exclamation bar) are sampled at the same polar angles so vertex i always maps to vertex i.
Coordinates are Root space (body centre 0, 0; y down). Each vertex: (x, y, in_dx, in_dy, out_dx, out_dy)."""
import math
K = 0.5523  # cubic arc constant for a quarter circle
def arc_pts(cx, cy, r, a0, a1, n):
    """n cubic vertices along a circular arc from angle a0 to a1 (degrees, screen orientation), handles as arc tangents."""
    out = []; step = (a1 - a0) / (n - 1) if n > 1 else 0; h = (4 / 3) * math.tan(math.radians(abs(step)) / 4) * r
    for i in range(n):
        a = math.radians(a0 + i * step); t = (-math.sin(a), math.cos(a)) if step >= 0 else (math.sin(a), -math.cos(a))
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        ins = (-t[0] * h, -t[1] * h) if i > 0 else (0, 0); outs = (t[0] * h, t[1] * h) if i < n - 1 else (0, 0)
        out.append([x, y, *ins, *outs])
    return out
def rounded_polygon(corners, r):
    """Corner list (clockwise, screen coords) -> vertices: two per corner at the fillet tangent points, straight edges between."""
    verts = []; n = len(corners)
    for i, c in enumerate(corners):
        p, nx = corners[i - 1], corners[(i + 1) % n]
        v1 = (p[0] - c[0], p[1] - c[1]); v2 = (nx[0] - c[0], nx[1] - c[1]); l1 = math.hypot(*v1); l2 = math.hypot(*v2)
        u1 = (v1[0] / l1, v1[1] / l1); u2 = (v2[0] / l2, v2[1] / l2)
        theta = math.acos(max(-1, min(1, u1[0] * u2[0] + u1[1] * u2[1])))           # interior angle
        d = r / math.tan(theta / 2)                                                      # distance from corner to tangent points
        a = (c[0] + u1[0] * d, c[1] + u1[1] * d); b = (c[0] + u2[0] * d, c[1] + u2[1] * d)
        h = (4 / 3) * math.tan((math.pi - theta) / 4) * r                               # handle length for the fillet arc
        verts.append([a[0], a[1], 0, 0, -u1[0] * h, -u1[1] * h])                         # fillet start: straight in, arc out (toward the corner)
        verts.append([b[0], b[1], -u2[0] * h, -u2[1] * h, 0, 0])                         # fillet end: arc in, straight out
    return verts
def offset_polygon(corners, d):
    """Offset a convex clockwise polygon outward by d (each edge moves out; corners are the offset-edge intersections)."""
    n = len(corners); out = []
    def edge_offset(i):
        a, b = corners[i], corners[(i + 1) % n]; ex, ey = b[0] - a[0], b[1] - a[1]; l = math.hypot(ex, ey)
        nx, ny = ey / l, -ex / l                      # outward normal for a clockwise polygon in screen coords
        # make sure it points away from the centroid
        cx = sum(c[0] for c in corners) / n; cy = sum(c[1] for c in corners) / n; mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        if (mx - cx) * nx + (my - cy) * ny < 0: nx, ny = -nx, -ny
        return (a[0] + nx * d, a[1] + ny * d), (b[0] + nx * d, b[1] + ny * d)
    for i in range(n):
        (p1, p2), (q1, q2) = edge_offset(i - 1), edge_offset(i)
        d1 = (p2[0] - p1[0], p2[1] - p1[1]); d2 = (q2[0] - q1[0], q2[1] - q1[1]); den = d1[0] * d2[1] - d1[1] * d2[0]
        t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
        out.append((p1[0] + d1[0] * t, p1[1] + d1[1] * t))
    return out
def circle(r, n=8, phase=-90):
    return arc_pts(0, 0, r, phase, phase + 360 - 360 / n, n) if False else _closed_arc(r, n, phase)
def _closed_arc(r, n, phase):
    out = []; step = 360 / n; h = (4 / 3) * math.tan(math.radians(step) / 4) * r
    for i in range(n):
        a = math.radians(phase + i * step); t = (-math.sin(a), math.cos(a))
        out.append([r * math.cos(a), r * math.sin(a), -t[0] * h, -t[1] * h, t[0] * h, t[1] * h])
    return out
BASE = {
    "circle": _closed_arc(88, 8, -90),
    "square": rounded_polygon([(-82, -82), (82, -82), (82, 82), (-82, 82)], 44),
    "hexagon": rounded_polygon(offset_polygon([(0, -86), (74, -43), (74, 43), (0, 86), (-74, 43), (-74, -43)], 11), 11),
    "triangle": rounded_polygon(offset_polygon([(0, -78), (80, 66), (-80, 66)], 15), 15),
    "pill": rounded_polygon([(-58, -88), (58, -88), (58, 88), (-58, 88)], 58),
    "arch": (lambda: (lambda top: top + [[78, 86, 0, 0, 0, 0], [-78, 86, 0, 0, 0, 0]])(arc_pts(0, -4, 78, 180, 360, 5)))(),
    "bean": None,
}
# bean: the canvas path M62 28 C124 8 178 48 174 108 C171 168 112 194 62 180 C22 168 14 122 30 92 C40 72 30 44 62 28 (body space -> Root space)
_b = [((62, 28), (124, 8), (30, 44)), ((174, 108), (171, 168), (178, 48)), ((62, 180), (22, 168), (112, 194)), ((30, 92), (40, 72), (14, 122))]
BASE["bean"] = [[p[0] - 100, p[1] - 100, i[0] - p[0], i[1] - p[1], o[0] - p[0], o[1] - p[1]] for p, o, i in _b]
def dedupe(verts):
    out = []
    for v in verts:
        if out and math.hypot(v[0] - out[-1][0], v[1] - out[-1][1]) < 0.5: out[-1] = [out[-1][0], out[-1][1], out[-1][2], out[-1][3], v[4], v[5]]
        else: out.append(list(v))
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < 0.5: out[0] = [out[0][0], out[0][1], out[-1][2], out[-1][3], out[0][4], out[0][5]]; out.pop()
    return out
for _k in list(BASE): BASE[_k] = dedupe(BASE[_k])
def angles(verts): return [math.degrees(math.atan2(v[1], v[0])) for v in verts]
def sample_outline(outline, angs, sharp=()):
    """Sample a star-shaped outline at the given angles; handles follow the outline's tangent (finite difference) and are
    scaled by the distance to each neighbour; vertices whose angle falls in a sharp window get zero-length handles."""
    n = len(angs); pts = [outline(a) for a in angs]; out = []
    for i in range(n):
        a = angs[i]; p = pts[i]; prev = pts[i - 1]; nxt = pts[(i + 1) % n]
        if any(lo <= a % 360 <= hi for lo, hi in sharp): out.append([p[0], p[1], 0, 0, 0, 0]); continue
        q0, q1 = outline(a - 0.5), outline(a + 0.5); t = (q1[0] - q0[0], q1[1] - q0[1]); tl = math.hypot(*t) or 1; t = (t[0] / tl, t[1] / tl)
        hin = math.hypot(p[0] - prev[0], p[1] - prev[1]) / 3; hout = math.hypot(nxt[0] - p[0], nxt[1] - p[1]) / 3
        out.append([p[0], p[1], -t[0] * hin, -t[1] * hin, t[0] * hout, t[1] * hout])
    return out
def ellipse(rx, ry): return lambda a: (rx * math.cos(math.radians(a)), ry * math.sin(math.radians(a)))
def drop(R=66, tail=44):
    """Teardrop: circle of radius R whose lower sector is replaced by the two tangent lines to the tip at (0, R + tail)."""
    tip = (0.0, R + tail); ta = math.degrees(math.acos(R / (R + tail)))      # tangent points sit at 90 +- ta
    def f(a):
        a = a % 360
        if 90 - ta < a < 90 + ta:                                                # on the cone: intersect the ray with the tangent line
            side = 1 if a > 90 else -1; tp = (R * math.cos(math.radians(90 + side * ta)), R * math.sin(math.radians(90 + side * ta)))
            dx, dy = math.cos(math.radians(a)), math.sin(math.radians(a)); ex, ey = tip[0] - tp[0], tip[1] - tp[1]
            den = dx * ey - dy * ex; s = (tp[0] * ey - tp[1] * ex) / den if den else 0
            return (dx * s, dy * s)
        return (R * math.cos(math.radians(a)), R * math.sin(math.radians(a)))
    return f
def exclaim(rx=27, ry=96, taper=0.35):
    def f(a):
        x, y = rx * math.cos(math.radians(a)), ry * math.sin(math.radians(a))
        if y > 0: x *= 1 - taper * (y / ry)
        return (x, y)
    return f
def targets(shape):
    base = BASE[shape]; angs = angles(base)
    # the vertex nearest the bottom becomes the drop's tip
    tipi = min(range(len(angs)), key=lambda i: abs((angs[i] % 360) - 90)); dangs = list(angs); dangs[tipi] = 90.0
    return {"NEUTRAL": base, "DROP": sample_outline(drop(), dangs, sharp=((89.9, 90.1),)), "DOT": sample_outline(ellipse(16, 16), angs), "PILL": sample_outline(ellipse(30, 88), angs), "EXCLAIM": sample_outline(exclaim(), angs)}
if __name__ == "__main__":
    import subprocess, sys
    # render every shape's base and targets as SVG to eyeball the geometry
    def path_d(verts):
        d = f"M{verts[0][0]:.1f} {verts[0][1]:.1f}"
        for i in range(len(verts)):
            a, b = verts[i], verts[(i + 1) % len(verts)]
            d += f" C{a[0] + a[4]:.1f} {a[1] + a[5]:.1f} {b[0] + b[2]:.1f} {b[1] + b[3]:.1f} {b[0]:.1f} {b[1]:.1f}"
        return d + " Z"
    rows = []
    for shape in BASE:
        t = targets(shape); cells = "".join(f'<g transform="translate({120 + j * 220} 0)"><path d="{path_d(v)}" fill="#FCADA3"/></g>' for j, v in enumerate(t.values()))
        rows.append(f'<g transform="translate(0 {120 + len(rows) * 220})">{cells}</g>')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1240 {120 + 220 * len(rows)}" width="1240"><rect width="100%" height="100%" fill="white"/>{"".join(rows)}</svg>'
    open("morph-check.svg", "w").write(svg); subprocess.run(["rsvg-convert", "-w", "1240", "morph-check.svg", "-o", "morph-check.png"], check=True); print("morph-check.png")

# ---------------------------------------------------------------- dense polygon variant: N straight vertices sampled by polar angle
def _flatten(verts, steps=40):
    """Flatten a closed cubic vertex loop into a dense point list."""
    pts = []; n = len(verts)
    for i in range(n):
        a, b = verts[i], verts[(i + 1) % n]; p0 = (a[0], a[1]); p1 = (a[0] + a[4], a[1] + a[5]); p2 = (b[0] + b[2], b[1] + b[3]); p3 = (b[0], b[1])
        for s in range(steps):
            t = s / steps; u = 1 - t
            pts.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0], u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return pts
def _ray_hit(pts, deg):
    """Point of a dense closed outline in direction deg from the origin (star-shaped outlines): the crossing of the ray."""
    dx, dy = math.cos(math.radians(deg)), math.sin(math.radians(deg)); best = None
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]; ex, ey = b[0] - a[0], b[1] - a[1]; den = dx * ey - dy * ex
        if abs(den) < 1e-9: continue
        s = (a[0] * ey - a[1] * ex) / den; u = (a[0] * dy - a[1] * dx) / den
        if s > 0 and 0 <= u <= 1 and (best is None or s > best): best = s
    return (dx * best, dy * best)
def _resample(pts, n):
    """n points equally spaced by arc length along a closed polyline, starting at the point closest to angle -90 (top)."""
    m = len(pts); start = min(range(m), key=lambda i: abs(((math.degrees(math.atan2(pts[i][1], pts[i][0])) + 90 + 180) % 360) - 180))
    loop = pts[start:] + pts[:start] + [pts[start]]
    seg = [math.hypot(loop[i + 1][0] - loop[i][0], loop[i + 1][1] - loop[i][1]) for i in range(len(loop) - 1)]; total = sum(seg)
    out = []; target = 0.0; i = 0; acc = 0.0
    for j in range(n):
        target = total * j / n
        while i < len(seg) - 1 and acc + seg[i] < target: acc += seg[i]; i += 1
        t = (target - acc) / seg[i] if seg[i] else 0
        out.append((loop[i][0] + (loop[i + 1][0] - loop[i][0]) * t, loop[i][1] + (loop[i + 1][1] - loop[i][1]) * t))
    return out
CORNERS = {"arch": [(-78, 86), (78, 86)]}   # sharp corners that must be exact vertices (no rounding)
def dense(shape, n=72):
    """Base polygon sampled by arc length (sharp corners pinned exactly); morph targets sampled at each base vertex's own angle, so vertex i maps to vertex i."""
    base = _resample(_flatten(BASE[shape]), n)
    for c in CORNERS.get(shape, []):
        j = min(range(n), key=lambda i: math.hypot(base[i][0] - c[0], base[i][1] - c[1])); base[j] = (float(c[0]), float(c[1]))
    angs = [math.degrees(math.atan2(y, x)) for x, y in base]
    tipi = min(range(n), key=lambda i: abs((angs[i] % 360) - 90)); dangs = list(angs); dangs[tipi] = 90.0
    ymax = max(y for _, y in base)
    def pinch(x, y):   # narrowed, slightly taller, tapered toward the bottom: the own-shape version of the teardrop squeeze
        t = max(0.0, y / ymax); return (x * 0.74 * (1 - 0.3 * t), y * 1.1)
    if shape == "circle": d = drop(); drop_pts = [d(a) for a in dangs]
    else: drop_pts = [pinch(x, y) for x, y in base]
    return {"NEUTRAL": base, "DOT": [(x * 0.18, y * 0.18) for x, y in base], "PILL": [(x * 0.34, y) for x, y in base], "EXCLAIM": [exclaim()(a) for a in angs], "DROP": drop_pts}
