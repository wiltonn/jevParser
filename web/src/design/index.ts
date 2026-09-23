// The design system: generated tokens, the vendored stylesheet, and the
// vendored component functions (window.JevDiff), which return HTML strings.
import "./tokens.gen.css";
import "@design/bundle.css";
import "@design/bundle.js";

type Html = string;
export interface JevDiffComponents {
  esc(s: unknown): string;
  Stat(value: unknown, label: string): Html;
  Tag(band: string): Html;
  BandPill(band: string, count: number, on?: boolean): Html;
  TrimMatrix(deltas: unknown[], oldLabel: string, newLabel: string): Html;
  EventCard(e: unknown, band: string, value: number, oldLabel: string, newLabel: string): Html;
  SheetTable(sheet: unknown, rows: unknown[], trims: unknown[], opts?: { hit?: string | null }): Html;
  KeyedTable(sheet: unknown, oldLabel: string, newLabel: string): Html;
  GridTable(sheet: unknown, oldLabel: string, newLabel: string): Html;
  AxisLineup(sheet: unknown, oldLabel: string, newLabel: string): Html;
  Nav(items: NavItem[], current: string, opts?: { vertical?: boolean }): Html;
}

export type NavItem =
  | { group: string }
  | { id: string; label: string; href: string; count?: number | null };

declare global {
  interface Window {
    JevDiff: JevDiffComponents;
  }
}

export const DS = (): JevDiffComponents => window.JevDiff;
