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
