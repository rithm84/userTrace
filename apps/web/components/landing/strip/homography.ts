import type { Point } from "./stripGeometry";

/**
 * The CSS matrix3d() that maps a width x height rectangle at the origin onto an
 * arbitrary quadrilateral (top-left, top-right, bottom-right, bottom-left). This is how
 * a real DOM screen sits on the drawn laptop: the dashboard stays live text and vector,
 * only its projection is perspective.
 */
export function quadTransform(width: number, height: number, quad: readonly [Point, Point, Point, Point]): string {
  const [[x0, y0], [x1, y1], [x2, y2], [x3, y3]] = quad;
  const sourceToUnit = adjugate(basisToPoints([0, 0], [width, 0], [width, height], [0, height]));
  const unitToTarget = basisToPoints([x0, y0], [x1, y1], [x2, y2], [x3, y3]);
  const h = multiply(unitToTarget, sourceToUnit);
  const n = h[8];
  const m = h.map((value) => value / n);
  // column-major 4x4 with the third row and column carrying z untouched
  const matrix = [m[0], m[3], 0, m[6], m[1], m[4], 0, m[7], 0, 0, 1, 0, m[2], m[5], 0, m[8]];
  return `matrix3d(${matrix.map((value) => value.toFixed(6)).join(",")})`;
}

type Mat = readonly [number, number, number, number, number, number, number, number, number];

function adjugate(m: Mat): Mat {
  return [
    m[4] * m[8] - m[5] * m[7], m[2] * m[7] - m[1] * m[8], m[1] * m[5] - m[2] * m[4],
    m[5] * m[6] - m[3] * m[8], m[0] * m[8] - m[2] * m[6], m[2] * m[3] - m[0] * m[5],
    m[3] * m[7] - m[4] * m[6], m[1] * m[6] - m[0] * m[7], m[0] * m[4] - m[1] * m[3],
  ];
}

function multiply(a: Mat, b: Mat): Mat {
  const out: number[] = [];
  for (let row = 0; row < 3; row++) {
    for (let col = 0; col < 3; col++) {
      out.push(a[row * 3] * b[col] + a[row * 3 + 1] * b[3 + col] + a[row * 3 + 2] * b[6 + col]);
    }
  }
  return out as unknown as Mat;
}

function multiplyVector(m: Mat, v: readonly [number, number, number]): readonly [number, number, number] {
  return [m[0] * v[0] + m[1] * v[1] + m[2] * v[2], m[3] * v[0] + m[4] * v[1] + m[5] * v[2], m[6] * v[0] + m[7] * v[1] + m[8] * v[2]];
}

function basisToPoints(p1: Point, p2: Point, p3: Point, p4: Point): Mat {
  const m: Mat = [p1[0], p2[0], p3[0], p1[1], p2[1], p3[1], 1, 1, 1];
  const v = multiplyVector(adjugate(m), [p4[0], p4[1], 1]);
  return multiply(m, [v[0], 0, 0, 0, v[1], 0, 0, 0, v[2]]);
}
