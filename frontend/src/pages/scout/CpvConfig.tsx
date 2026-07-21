import { useEffect, useMemo, useState } from 'react';
import { getCpvConfig, putCpvConfig } from '../../api';
import type { CpvConfigEntry } from '../../types';

function groupLabel(group: string | null): string {
  if (!group) return '—';
  return group.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

export default function CpvConfig() {
  const [entries, setEntries] = useState<CpvConfigEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [newCode, setNewCode] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState('');
  const [removingCode, setRemovingCode] = useState<string | null>(null);

  function load(): Promise<CpvConfigEntry[]> {
    return getCpvConfig().then(data => {
      setEntries(data);
      return data;
    });
  }

  useEffect(() => {
    load().catch(() => setError('Failed to load CPV configuration')).finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(e =>
      e.code.toLowerCase().includes(q) ||
      e.labels.en?.toLowerCase().includes(q) ||
      e.labels.fr?.toLowerCase().includes(q) ||
      e.labels.nl?.toLowerCase().includes(q) ||
      e.labels.de?.toLowerCase().includes(q)
    );
  }, [entries, search]);

  async function removeCode(code: string) {
    setRemovingCode(code);
    try {
      const remaining = entries.filter(e => e.code !== code).map(e => e.code);
      await putCpvConfig(remaining);
      setEntries(entries.filter(e => e.code !== code));
    } catch {
      setError('Failed to save — this code was not removed');
    } finally {
      setRemovingCode(null);
    }
  }

  async function addCode(e: React.FormEvent) {
    e.preventDefault();
    const code = newCode.trim();
    if (!code) return;
    setAddError('');
    if (entries.some(en => en.code === code)) {
      setAddError(`${code} is already in the active CPV set`);
      return;
    }
    setAdding(true);
    try {
      const currentCodes = entries.map(en => en.code);
      const result = await putCpvConfig([...currentCodes, code]);
      if (result.warnings.some(w => w.includes(code))) {
        // Backend saves unknown codes anyway and only warns — strip it back
        // out so an invalid code is never left in the active set (§4:
        // "validate ... then write", not "write, then warn").
        await putCpvConfig(currentCodes);
        setAddError(`"${code}" is not a recognized CPV code (checked against cpv_reference.json) — not added`);
      } else {
        await load();
        setNewCode('');
      }
    } catch {
      setAddError('Failed to reach the server — try again');
    } finally {
      setAdding(false);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;
  if (error) return <div className="error">{error}</div>;

  return (
    <div>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        Your active CPV code watchlist — codes matched here drive the primary (high-confidence) match signal
      </p>

      {/* Add CPV */}
      <form onSubmit={addCode} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ flex: '0 1 280px' }}>
          <input
            className="input-field"
            placeholder="+ Add CPV code, e.g. 39522530"
            value={newCode}
            onChange={ev => { setNewCode(ev.target.value); setAddError(''); }}
            disabled={adding}
          />
        </div>
        <button type="submit" className="btn btn-teal" disabled={adding || !newCode.trim()}>
          {adding ? 'Adding…' : '+ Add CPV'}
        </button>
      </form>
      {addError && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{addError}</div>}

      {/* Search */}
      <div style={{ position: 'relative', maxWidth: 360, marginBottom: 16 }}>
        <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#4c5a70', fontSize: 14 }}>⌕</span>
        <input
          className="input-field"
          style={{ paddingLeft: 30 }}
          placeholder="Search code or label…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      <div className="card">
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #1a2334', color: '#8892a4', fontSize: 12 }}>
          {filtered.length} of {entries.length} active codes
        </div>
        {filtered.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No CPV codes match your search.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Code</th>
                <th>EN</th>
                <th>FR</th>
                <th>NL</th>
                <th>DE</th>
                <th>Group</th>
                <th>Active</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(en => (
                <tr key={en.code}>
                  <td className="mono" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>{en.code}</td>
                  <td style={{ fontSize: 13 }}>{en.labels.en || '—'}</td>
                  <td style={{ fontSize: 13 }}>{en.labels.fr || '—'}</td>
                  <td style={{ fontSize: 13 }}>{en.labels.nl || '—'}</td>
                  <td style={{ fontSize: 13 }}>{en.labels.de || '—'}</td>
                  <td style={{ fontSize: 12, color: '#8892a4', whiteSpace: 'nowrap' }}>{groupLabel(en.group)}</td>
                  <td>
                    <button
                      role="switch"
                      aria-checked="true"
                      title="Active — click to remove from the active CPV set"
                      onClick={() => removeCode(en.code)}
                      disabled={removingCode === en.code}
                      style={{
                        width: 36, height: 20, borderRadius: 9999, border: 'none', position: 'relative',
                        background: '#2EE6D4', opacity: removingCode === en.code ? 0.5 : 1, flexShrink: 0,
                      }}
                    >
                      <span style={{
                        position: 'absolute', top: 2, right: 2, width: 16, height: 16, borderRadius: '50%',
                        background: '#0f1623',
                      }} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
