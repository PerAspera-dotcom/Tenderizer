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

const ISO3_FLAGS: Record<string, string> = {
  SWE: '🇸🇪', POL: '🇵🇱', FRA: '🇫🇷', FR: '🇫🇷',
  DEU: '🇩🇪', BEL: '🇧🇪', DNK: '🇩🇰', NLD: '🇳🇱',
  ESP: '🇪🇸', GBR: '🇬🇧', ITA: '🇮🇹', NOR: '🇳🇴',
  AUT: '🇦🇹', CHE: '🇨🇭', CZE: '🇨🇿', HUN: '🇭🇺',
  ROU: '🇷🇴', FIN: '🇫🇮', GRC: '🇬🇷', PRT: '🇵🇹',
  SVK: '🇸🇰', SVN: '🇸🇮', HRV: '🇭🇷', BGR: '🇧🇬',
  LTU: '🇱🇹', LVA: '🇱🇻', EST: '🇪🇪', LUX: '🇱🇺',
  CYP: '🇨🇾', MLT: '🇲🇹', IRL: '🇮🇪', EU: '🇪🇺',
};

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

export function matchLabel(ms: string | null | undefined): string {
  if (!ms || ms === 'None' || ms === 'none') return 'Low';
  if (ms === 'both') return 'Both';
  if (ms === 'cpv') return 'CPV';
  if (ms === 'keyword') return 'Keyword';
  return ms;
}
