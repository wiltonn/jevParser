// Shapes returned by the jev_web API (src/jev_web/api/serialize.py).

export interface User { id: number; email: string; display_name: string; role: "admin" | "analyst"; is_active: boolean }

export interface CatalogYear { id: number; year: number; variant_label: string | null; documents: number }
export interface CatalogModel { id: number; slug: string; name: string; years: CatalogYear[] }
export interface CatalogBrand { id: number; slug: string; name: string; models: CatalogModel[] }
export interface CatalogOem { id: number; slug: string; name: string; prompt_name: string; brands: CatalogBrand[] }

export interface ModelYearRef {
  id: number; year: number; variant_label: string | null;
  model_id: number; model: string; brand_id: number; brand: string; oem_id: number; oem: string;
}

export interface SheetSummary { name: string; kind: string; rows: number; columns: number; footnotes: number; images: number }

export interface ParseRun {
  id: number; status: "queued" | "running" | "ok" | "warnings" | "fatal" | "failed";
  fatal: number; warnings: number; error: string | null;
  ruleset_version_id: number; ruleset: string; ruleset_version: number;
  sheets: SheetSummary[] | null; relations: Record<string, number> | null;
  started_at: string | null; finished_at: string | null;
}

export interface Suggestion {
  ruleset_id: number; oem_id: number; oem: string;
  brand_id: number | null; brand: string | null;
  model_id: number | null; model: string | null;
  year: number | null; model_year_id: number | null;
  confidence: number; sheet_match: number; source: string;
}

export interface Doc {
  id: number; filename: string; format: string; size: number; sha256: string;
  model_year: ModelYearRef | null;
  detection_status: "pending" | "confirmed" | "rejected";
  is_primary: boolean; uploaded_at: string; notes: string | null;
  parse: ParseRun | null;
  detection: { sheet_names: string[]; suggestions: Suggestion[]; duplicate_of?: number } | null;
  parse_runs?: ParseRun[];
}

export interface Job {
  id: number; type: string; status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string | null; progress: number; message: string | null; error: string | null;
  result: any; params: any; cancel_requested: boolean;
  comparison_id: number | null; document_id: number | null;
  created_at: string; started_at: string | null; finished_at: string | null;
}

export interface ComparisonSide { year_id: number; year: number; document_id: number; filename: string }

export interface Comparison {
  id: number; status: Job["status"]; error: string | null;
  model_id: number; model: string; brand: string; oem: string;
  old: ComparisonSide; new: ComparisonSide;
  ruleset: { id: number; version_id: number; name: string; version: number };
  options: { judge: boolean; workers: number };
  timeline_id: number | null; timeline_position: number | null;
  created_at: string; finished_at: string | null;
  summary: any | null; usage: any | null;
  cache: { hits: number; misses: number } | null;
  judge_errors: string[] | null;
  job: Job | null;
}

export interface ModelOverview {
  id: number; name: string; slug: string;
  brand: { id: number; name: string }; oem: { id: number; name: string };
  years: { id: number; year: number; variant_label: string | null; documents: Doc[] }[];
  comparisons: Comparison[];
  timelines: { id: number; name: string; created_at: string; steps: number }[];
}

export interface RulesetVersion {
  id: number; ruleset_id: number; version: number; status: "draft" | "published" | "archived";
  schema_version: number; content_hash: string; notes: string | null;
  created_at: string; published_at: string | null; body?: any;
  ruleset?: Ruleset;
}

export interface Ruleset {
  id: number; name: string; description: string | null;
  oem_id: number; oem: string; brand_id: number | null; brand: string | null;
  model_id: number | null; model: string | null;
  year_from: number | null; year_to: number | null; format: string; priority: number;
  created_at: string; published_version: number | null; has_draft: boolean;
  cloned_from_version_id: number | null;
  versions?: RulesetVersion[];
}

export interface ResolveCandidate {
  ruleset_id: number; ruleset: string; version_id: number; version: number;
  specificity: "model" | "brand" | "oem"; priority: number; format: string;
}

export interface Timeline {
  id: number; name: string; model: { id: number; name: string; brand: string };
  steps: Comparison[]; recurring_codes: { code: string; steps: number[] }[];
}

// The changeset payload (jev_diff/render/payload.py).
export interface Delta {
  axis: string; trim: string; direction: string; from: string; to: string; changed: boolean;
  added: string[]; dropped: string[]; reworded: [string, string][];
  cell_old: string | null; cell_new: string | null;
}
export interface PEvent {
  eid: string; kind: string; sheet: string; also_on: string[]; code: string | null;
  description: string; detail: string; disposition: string | null; tier: number | null;
  order_affecting: boolean; rows: [string, string, string][];
  judgments: Record<string, { score: number | null; noul: number | null; choice: string | null; confidence: number | null; probabilities: any }>;
  deltas: Delta[];
}
export interface PRow {
  rid: string; code: string | null; orderable: string | null; ref: string | null; desc: string;
  section: string | null; cell: string; eid?: string | null;
  v: Record<string, { t: string; val: string | null; c: string[] }>;
}
export interface PAxis { axis: string; name: string; code: string | null }
export interface PSheet {
  name: string; slug: string; kind: string; shape: "feature" | "keyed" | "grid";
  trims: { old: PAxis[]; new: PAxis[] };
  axis: any;
  rows: { old: PRow[]; new: PRow[] };
  counts: { rows_old: number; rows_new: number; paired: number; changed: number };
}
export interface Payload {
  old: string; new: string; source: any;
  summary: {
    sheets: number; rows_old: number; rows_new: number; paired: number; tiers: Record<string, number>;
    codes: number; multi_sheet_codes: number; requests: number; input_tokens: number;
    cache_hits: number; residue: number; judged: number; needs_review: number;
  };
  lenses: Record<string, { weights: Record<string, number>; bands: { material: number; review: number }; confidence_floor: number }>;
  sheets: PSheet[];
  events: PEvent[];
  residue: { uid: string; sheet: string; year: string; code: string | null; description: string; disposition: string; verdict: string }[];
  pairings: { jid: string; key: string; outcome: string; score: number | null; confidence: number | null; succession: number | null; absorbed: number | null }[];
  warnings: { kind: string; severity: string; where: string; message: string }[];
}
