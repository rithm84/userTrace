"""Flatten a composed accessory SVG fragment: bake nested group transforms into absolute path coordinates and convert arcs to cubics,
so the Rive importer sees only M/L/C/Z paths and plain circles."""
import math, re
import xml.etree.ElementTree as ET
NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
def parse_transform(t):
    m = [[1, 0, 0], [0, 1, 0]]
    def mul(a, b): return [[a[0][0]*b[0][0]+a[0][1]*b[1][0], a[0][0]*b[0][1]+a[0][1]*b[1][1], a[0][0]*b[0][2]+a[0][1]*b[1][2]+a[0][2]], [a[1][0]*b[0][0]+a[1][1]*b[1][0], a[1][0]*b[0][1]+a[1][1]*b[1][1], a[1][0]*b[0][2]+a[1][1]*b[1][2]+a[1][2]]]
    for name, args in re.findall(r"(translate|scale|rotate)\(([^)]*)\)", t or ""):
        v = [float(x) for x in re.findall(NUM, args)]
        if name == "translate": b = [[1, 0, v[0]], [0, 1, v[1] if len(v) > 1 else 0]]
        elif name == "scale": sx = v[0]; sy = v[1] if len(v) > 1 else sx; b = [[sx, 0, 0], [0, sy, 0]]
        else:
            a = math.radians(v[0]); c, s = math.cos(a), math.sin(a); cx, cy = (v[1], v[2]) if len(v) > 2 else (0, 0)
            b = [[c, -s, cx - c*cx + s*cy], [s, c, cy - s*cx - c*cy]]
        m = mul(m, b)
    return m
def compose(a, b):
    return [[a[0][0]*b[0][0]+a[0][1]*b[1][0], a[0][0]*b[0][1]+a[0][1]*b[1][1], a[0][0]*b[0][2]+a[0][1]*b[1][2]+a[0][2]], [a[1][0]*b[0][0]+a[1][1]*b[1][0], a[1][0]*b[0][1]+a[1][1]*b[1][1], a[1][0]*b[0][2]+a[1][1]*b[1][2]+a[1][2]]]
def apply(m, x, y): return (m[0][0]*x + m[0][1]*y + m[0][2], m[1][0]*x + m[1][1]*y + m[1][2])
def uniform_scale(m): return math.sqrt(abs(m[0][0]*m[1][1] - m[0][1]*m[1][0]))
def arc_to_cubics(x1, y1, rx, ry, phi, fa, fs, x2, y2):
    """SVG endpoint arc -> list of cubic segments (each (c1x,c1y,c2x,c2y,x,y)), per the SVG implementation notes."""
    if rx == 0 or ry == 0: return [(x1, y1, x2, y2, x2, y2)]
    phi = math.radians(phi); cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2; x1p = cp*dx + sp*dy; y1p = -sp*dx + cp*dy
    rx, ry = abs(rx), abs(ry); lam = (x1p/rx)**2 + (y1p/ry)**2
    if lam > 1: rx *= math.sqrt(lam); ry *= math.sqrt(lam)
    num = rx*rx*ry*ry - rx*rx*y1p*y1p - ry*ry*x1p*x1p; den = rx*rx*y1p*y1p + ry*ry*x1p*x1p
    coef = (1 if fa != fs else -1) * math.sqrt(max(0, num / den)) if den else 0
    cxp, cyp = coef * rx*y1p/ry, coef * -ry*x1p/rx
    cx = cp*cxp - sp*cyp + (x1 + x2)/2; cy = sp*cxp + cp*cyp + (y1 + y2)/2
    def ang(ux, uy, vx, vy):
        a = math.atan2(ux*vy - uy*vx, ux*vx + uy*vy); return a
    t1 = ang(1, 0, (x1p - cxp)/rx, (y1p - cyp)/ry); dt = ang((x1p - cxp)/rx, (y1p - cyp)/ry, (-x1p - cxp)/rx, (-y1p - cyp)/ry)
    if not fs and dt > 0: dt -= 2*math.pi
    if fs and dt < 0: dt += 2*math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2)))); segs = []; t = t1; step = dt / n
    for _ in range(n):
        a1, a2 = t, t + step; k = 4/3 * math.tan((a2 - a1) / 4)
        def pt(a): return (cx + rx*math.cos(a)*cp - ry*math.sin(a)*sp, cy + rx*math.cos(a)*sp + ry*math.sin(a)*cp)
        def der(a): return (-rx*math.sin(a)*cp - ry*math.cos(a)*sp, -rx*math.sin(a)*sp + ry*math.cos(a)*cp)
        p1, p2 = pt(a1), pt(a2); d1, d2 = der(a1), der(a2)
        segs.append((p1[0] + k*d1[0], p1[1] + k*d1[1], p2[0] - k*d2[0], p2[1] - k*d2[1], p2[0], p2[1])); t = a2
    return segs
def _subpaths(d, m):
    """Parse a path into subpaths of absolute segments in the target space: each subpath is [(x0,y0), seg, ...] with seg = ("L",x,y) or ("C",c1x,c1y,c2x,c2y,x,y)."""
    toks = re.findall(r"[MLCAZmlcaz]|" + NUM, d); i = 0; cur = (0.0, 0.0); start = (0.0, 0.0); cmd = None; subs = []; sp = None
    T = lambda x, y: apply(m, x, y)
    while i < len(toks):
        if re.match(r"[A-Za-z]", toks[i]): cmd = toks[i]; i += 1
        rel = cmd.islower(); c = cmd.upper()
        if c == "Z":
            if sp is not None and (not sp or sp[-1] != ("Z",)): sp.append(("Z",))
            cur = start; continue
        if c == "M":
            x, y = float(toks[i]), float(toks[i+1]); i += 2
            if rel: x += cur[0]; y += cur[1]
            cur = start = (x, y); sp = [T(x, y)]; subs.append(sp); cmd = "l" if rel else "L"
        elif c == "L":
            x, y = float(toks[i]), float(toks[i+1]); i += 2
            if rel: x += cur[0]; y += cur[1]
            cur = (x, y); sp.append(("L",) + T(x, y))
        elif c == "C":
            v = [float(t) for t in toks[i:i+6]]; i += 6
            if rel: v = [v[0]+cur[0], v[1]+cur[1], v[2]+cur[0], v[3]+cur[1], v[4]+cur[0], v[5]+cur[1]]
            sp.append(("C",) + T(v[0], v[1]) + T(v[2], v[3]) + T(v[4], v[5])); cur = (v[4], v[5])
        elif c == "A":
            v = [float(t) for t in toks[i:i+7]]; i += 7
            x2, y2 = (v[5] + cur[0], v[6] + cur[1]) if rel else (v[5], v[6])
            for sg in arc_to_cubics(cur[0], cur[1], v[0], v[1], v[2], int(v[3]), int(v[4]), x2, y2): sp.append(("C",) + T(sg[0], sg[1]) + T(sg[2], sg[3]) + T(sg[4], sg[5]))
            cur = (x2, y2)
    return [s for s in subs if len(s) > 1]
def _poly(sp, n=8):
    pts = [sp[0]]; cur = sp[0]
    for seg in [x for x in sp[1:] if x != ("Z",)]:
        if seg[0] == "L": pts.append((seg[1], seg[2])); cur = (seg[1], seg[2])
        else:
            for k in range(1, n + 1):
                t = k / n; u = 1 - t
                pts.append((u*u*u*cur[0] + 3*u*u*t*seg[1] + 3*u*t*t*seg[3] + t*t*t*seg[5], u*u*u*cur[1] + 3*u*u*t*seg[2] + 3*u*t*t*seg[4] + t*t*t*seg[6]))
            cur = (seg[5], seg[6])
    return pts
def _area(poly): return 0.5 * sum(poly[i][0]*poly[(i+1) % len(poly)][1] - poly[(i+1) % len(poly)][0]*poly[i][1] for i in range(len(poly)))
def _inside(pt, poly):
    x, y = pt; ins = False; n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]; x2, y2 = poly[(i+1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: ins = not ins
    return ins
def _reverse(sp):
    """Reverse a subpath's direction (cubic control points swap)."""
    closed = sp[-1] == ("Z",); segs = [x for x in sp[1:] if x != ("Z",)]; anchors = [sp[0]] + [((s[5], s[6]) if s[0] == "C" else (s[1], s[2])) for s in segs]
    out = [anchors[-1]]
    for i in range(len(segs) - 1, -1, -1):
        s = segs[i]; prev = anchors[i]
        out.append(("L", prev[0], prev[1]) if s[0] == "L" else ("C", s[3], s[4], s[1], s[2], prev[0], prev[1]))
    return out + ([("Z",)] if closed else [])
def transform_path(d, m):
    """Absolute, arc-free path data with consistent winding: outer subpaths positive, subpaths nested inside another negative (real holes)."""
    subs = _subpaths(d, m); polys = [_poly(s) for s in subs]; out = []
    for i, sp in enumerate(subs):
        closed = sp[-1] == ("Z",)
        if closed:
            hole = any(j != i and abs(_area(polys[j])) > abs(_area(polys[i])) and _inside(polys[i][0], polys[j]) for j in range(len(subs)))
            if (_area(polys[i]) > 0) != (not hole): sp = _reverse(sp)
        segs = [x for x in sp[1:] if x != ("Z",)]
        parts = [f"M {sp[0][0]:.3f} {sp[0][1]:.3f}"]; cur = sp[0]
        for seg in segs:
            if seg[0] == "L": parts.append(f"C {cur[0]:.3f} {cur[1]:.3f} {seg[1]:.3f} {seg[2]:.3f} {seg[1]:.3f} {seg[2]:.3f}"); cur = (seg[1], seg[2])
            else: parts.append("C " + " ".join(f"{v:.3f}" for v in seg[1:])); cur = (seg[5], seg[6])
        if closed:
            if abs(cur[0] - sp[0][0]) > 1e-6 or abs(cur[1] - sp[0][1]) > 1e-6:
                parts.append(f"C {cur[0]:.3f} {cur[1]:.3f} {sp[0][0]:.3f} {sp[0][1]:.3f} {sp[0][0]:.3f} {sp[0][1]:.3f}")
            parts.append("Z")
        out.append(" ".join(parts))
    return " ".join(out)
def flatten(fragment):
    root = ET.fromstring(f'<svg xmlns="http://www.w3.org/2000/svg">{fragment}</svg>'); out = []
    def walk(el, m, style):
        tag = el.tag.split("}")[-1]; st = dict(style)
        for k in ("fill", "stroke", "stroke-width", "paint-order", "opacity", "stroke-linecap", "stroke-linejoin"):
            if el.get(k) is not None: st[k] = el.get(k)
        mm = compose(m, parse_transform(el.get("transform")))
        if tag == "g":
            for ch in el: walk(ch, mm, st)
        elif tag == "path":
            attrs = {k: v for k, v in st.items()}
            if "stroke-width" in attrs: attrs["stroke-width"] = f"{float(attrs['stroke-width']) * uniform_scale(mm):.3f}"
            out.append(f'<path d="{transform_path(el.get("d"), mm)}" ' + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + "/>")
        elif tag == "circle":
            cx, cy = apply(mm, float(el.get("cx")), float(el.get("cy"))); r = float(el.get("r")) * uniform_scale(mm)
            attrs = {k: v for k, v in st.items()}
            if "stroke-width" in attrs: attrs["stroke-width"] = f"{float(attrs['stroke-width']) * uniform_scale(mm):.3f}"
            out.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" ' + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + "/>")
        elif tag == "rect":
            x, y, w, h = (float(el.get(k)) for k in ("x", "y", "width", "height")); rx = float(el.get("rx") or 0)
            s = uniform_scale(mm); p = apply(mm, x, y)
            attrs = {k: v for k, v in st.items()}
            out.append(f'<rect x="{p[0]:.3f}" y="{p[1]:.3f}" width="{w*s:.3f}" height="{h*s:.3f}" rx="{rx*s:.3f}" ' + " ".join(f'{k}="{v}"' for k, v in attrs.items()) + "/>")
    for ch in root: walk(ch, [[1, 0, 0], [0, 1, 0]], {})
    return "".join(out)
