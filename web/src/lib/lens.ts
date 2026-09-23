// Lens arithmetic, ported from jev_diff/judge/run.py (lens_value, band).
// Pure functions over stored judgments: moving a slider re-bands every event
// with no request to the server.  lens.test.ts holds it to the Python output.

export interface Answer {
  score?: number | null;
  confidence?: number | null;
  [k: string]: unknown;
}
export type Judgments = Record<string, Answer | undefined>;

export interface LensSettings {
  weights: Record<string, number>;
  bands: { material: number; review: number };
  confidence_floor: number;
}

export type Band = "material" | "review" | "cosmetic" | "unjudged";

export function lensValue(j: Judgments, lens: LensSettings): number {
  let total = 0;
  let divisor = 0;
  for (const [q, w] of Object.entries(lens.weights)) {
    const s = j[q]?.score;
    total += w * (s == null ? 0 : s);
    divisor += Math.abs(w);
  }
  return divisor ? total / divisor : 0;
}

export function minConfidence(j: Judgments, lens: LensSettings): number | null {
  const cs: number[] = [];
  for (const [q, w] of Object.entries(lens.weights)) {
    const c = j[q]?.confidence;
    if (w > 0 && c != null) cs.push(c);
  }
  return cs.length ? Math.min(...cs) : null;
}

export function band(j: Judgments, lens: LensSettings): Band {
  if (!Object.keys(j).length) return "unjudged";
  const v = lensValue(j, lens);
  if (v >= lens.bands.material) {
    const c = minConfidence(j, lens);
    return c !== null && c < lens.confidence_floor ? "review" : "material";
  }
  if (v >= lens.bands.review) return "review";
  return "cosmetic";
}
