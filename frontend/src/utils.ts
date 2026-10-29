import type { Tender } from './types';

// CR-001 R3: a tender needs translation when its source language isn't English.
// Shared across every screen that lists tender titles (Scout + Portal) so
// "all scraped tenders" actually means all of them, not just Review Queue.
export function needsTranslation(t: Pick<Tender, 'language'>): boolean {
  return !!t.language && t.language !== 'eng' && t.language !== 'en';
}

// CR-005 follow-up: run.py's per-run translation step makes two independent
// DeepL calls (tag_line, description) and only marks the combined
// `translation_status` 'ok' when BOTH succeed — so a tender where the
// (short) title call succeeded but the (longer) description call didn't
// — e.g. a free-tier monthly quota that's enough for one call but not the
// other — sits at translation_status='unavailable' indefinitely, even
// though tag_line_en holds a perfectly good translation. Gating display on
// the combined status (as this used to) threw that real, already-paid-for
// translation away and showed the original for both fields. Each field now
// falls back independently on its own emptiness, not the combined status.
export function displayTagLine(
  t: Pick<Tender, 'tag_line' | 'tag_line_en' | 'language'>,
  showOriginal = false,
): string {
  if (!needsTranslation(t) || showOriginal) return t.tag_line;
  return t.tag_line_en || t.tag_line;
}

export function displayDescription(
  t: Pick<Tender, 'description' | 'description_en' | 'language'>,
  showOriginal = false,
): string {
  if (!needsTranslation(t) || showOriginal) return t.description;
  return t.description_en || t.description;
}

export function hasTranslatedTagLine(t: Pick<Tender, 'tag_line_en' | 'language'>): boolean {
  return needsTranslation(t) && !!t.tag_line_en;
}

export function hasTranslatedDescription(t: Pick<Tender, 'description_en' | 'language'>): boolean {
  return needsTranslation(t) && !!t.description_en;
}

export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  } catch {
    return '—';
  }
}

export function formatTime(dateStr: string | null | undefined): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  } catch {
    return '—';
  }
}

export function daysLeft(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return null;
  return Math.ceil((d.getTime() - Date.now()) / 86400000);
}

// CR-007 Phase D (D2): the deadline window setting is hours-granular (e.g.
// 72), so the day-rounded daysLeft above is too coarse to compare it
// against fairly near a boundary.
export function hoursLeft(dateStr: string | null | undefined): number | null {
  if (!dateStr) return null;
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return null;
  return (d.getTime() - Date.now()) / 3600000;
}

const ISO3_FLAGS: Record<string, string> = {
  SWE: '🇸🇪', POL: '🇵🇱', FRA: '🇫🇷', FR: '🇫🇷',
  DEU: '🇩🇪', BEL: '🇧🇪', DNK: '🇩🇰', NLD: '🇳🇱',
  ESP: '🇪🇸', GBR: '🇬🇧', ITA: '🇮🇹', NOR: '🇳🇴',
  AUT: '🇦🇹', CHE: '🇨🇭', CZE: '🇨🇿', HUN: '🇭🇺',
  ROU: '🇷🇴', FIN: '🇫🇮', GRC: '🇬🇷', PRT: '🇵🇹',
  SVK: '🇸🇰', SVN: '🇸🇮', HRV: '🇭🇷', BGR: '🇧🇬',
  LTU: '🇱🇹', LVA: '🇱🇻', EST: '🇪🇪', LUX: '🇱🇺',
  CYP: '🇨🇾', MLT: '🇲🇹', IRL: '🇮🇪', EU: '🇪🇺',
  // Non-EU sources (CR-011 §4) + the 2-letter codes the Belgium/Germany
  // connectors store ("BE"/"DE", like BOAMP's "FR").
  CAN: '🇨🇦', PHL: '🇵🇭', DE: '🇩🇪', BE: '🇧🇪',
};

// Scout portal filter options. `value` must be the exact `source` string the
// connector stores (GET /api/tenders?source= matches it, case-insensitively).
export const PORTAL_OPTS: { value: string; label: string }[] = [
  { value: '', label: 'All portals' },
  { value: 'TED', label: 'TED (EU)' },
  { value: 'BOAMP', label: 'BOAMP (France)' },
  { value: 'e-Procurement', label: 'e-Procurement (Belgium)' },
  { value: 'Bekanntmachungsservice', label: 'Bekanntmachungsservice (Germany)' },
  { value: 'TenderNed', label: 'TenderNed (Netherlands)' },
  { value: 'Find a Tender', label: 'Find a Tender (UK)' },
  { value: 'Contracts Finder', label: 'Contracts Finder (UK)' },
  { value: 'CanadaBuys', label: 'CanadaBuys (Canada)' },
  { value: 'PhilGEPS', label: 'PhilGEPS (Philippines)' },
];

// Home page per source — the link shown when a notice has no page of its own
// (e.g. a CanadaBuys row with no notice URL), so there's always somewhere to go.
export const PORTAL_HOME: Record<string, string> = {
  'TED': 'https://ted.europa.eu/',
  'BOAMP': 'https://www.boamp.fr/',
  'e-Procurement': 'https://www.publicprocurement.be/',
  'Bekanntmachungsservice': 'https://www.oeffentlichevergabe.de/',
  'Find a Tender': 'https://www.find-tender.service.gov.uk/',
  'Contracts Finder': 'https://www.contractsfinder.service.gov.uk/',
  'CanadaBuys': 'https://canadabuys.canada.ca/en/tender-opportunities',
  'TenderNed': 'https://www.tenderned.nl/',
  'PhilGEPS': 'https://notices.philgeps.gov.ph/',
  'AusTender': 'https://www.tenders.gov.au/',
  'GeBIZ': 'https://www.gebiz.gov.sg/',
  'Etimad': 'https://tenders.etimad.sa/',
  'ePerolehan': 'https://www.eperolehan.gov.my/',
};

// Attribution + disclaimer for sources whose reuse permission requires them
// (BOSA's e-Procurement go-live, 2026-10-29). Keep the wording identical to
// src/attribution.py, which appends it to outgoing tender emails.
export const SOURCE_NOTICES: Record<string, { attribution: string; disclaimer: string }> = {
  'e-Procurement': {
    attribution: 'Source: Belgian e-Procurement platform (publicprocurement.be), FPS BOSA.',
    disclaimer: 'Tenderizer is not affiliated with or endorsed by FPS BOSA. Notice data is '
      + 'reproduced as published; FPS BOSA gives no guarantee as to its accuracy or '
      + 'currency. Always check the original publication before acting on it.',
  },
};

export const PORTAL_FALLBACK_TITLE ='No page for this notice — opens the portal home page';

// The notice's own page, else its portal's home page (fallback: true), else null.
export function tenderLink(t: { url?: string | null; source?: string | null }): { href: string; fallback: boolean } | null {
  if (t.url) return { href: t.url, fallback: false };
  const home = t.source ? PORTAL_HOME[t.source] : undefined;
  return home ? { href: home, fallback: true } : null;
}

export function countryFlag(code: string | null | undefined): string {
  if (!code) return '';
  return ISO3_FLAGS[code.toUpperCase()] ?? code;
}

export function confidenceFromMatchSource(ms: string | null | undefined): number {
  if (!ms || ms === 'None' || ms === 'none') return 40;
  if (ms === 'both') return 92;
  if (ms === 'cpv') return 88;
  if (ms === 'keyword') return 65;
  return 40;
}

// CR-002 C4: value is a raw numeric string from the source notice (CR-001 F6);
// omit entirely when absent rather than showing a placeholder like "$0"/"—".
export function formatValue(value: string | null | undefined, currency: string | null | undefined): string | null {
  if (!value) return null;
  const n = Number(value);
  if (isNaN(n)) return null;
  const amount = n.toLocaleString('en-GB');
  return currency ? `${amount} ${currency}` : amount;
}

// CR-008 W5: CPV codes are hierarchical by trailing zeros — e.g. "39516100"
// (camp beds) is more specific than "39500000" (textiles). Fewer trailing
// zeros in the 8-digit code = more specific; sorts ascending so the most
// specific code comes first. Ties (equal specificity) keep their relative
// order (Array.prototype.sort is stable).
export function sortCpvCodesBySpecificity(codes: string[]): string[] {
  function trailingZeros(code: string): number {
    const digits = code.slice(0, 8);
    return digits.length - digits.replace(/0+$/, '').length;
  }
  return [...codes].sort((a, b) => trailingZeros(a) - trailingZeros(b));
}

export function matchLabel(ms: string | null | undefined): string {
  if (!ms || ms === 'None' || ms === 'none') return 'Low';
  if (ms === 'both') return 'Both';
  if (ms === 'cpv') return 'CPV';
  if (ms === 'keyword') return 'Keyword';
  return ms;
}
