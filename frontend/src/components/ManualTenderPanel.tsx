// CR-013 §4/§6 — shown on a manually entered tender in the Review Queue:
// the "Entered by hand" marker, the exclusion an automated rule would have
// applied (it never hides a manual tender), and edit / delete. Delete is a
// soft delete — the record, its pipeline row and history are kept and it can
// be restored — and asks for a second click rather than a browser dialog.
import { useState } from 'react';
import { deleteManualTender } from '../api';
import { useNavigate } from '../router';
import type { Tender } from '../types';
import { formatDate } from '../utils';

export function ManualBadge() {
  return <span className="pill pill-purple" title="Entered by hand, not scraped">Manual</span>;
}

export function WouldBeExcluded({ reason }: { reason?: string }) {
  if (!reason) return null;
  return (
    <span className="pill pill-amber" title="An automated filter would hide this tender; it is shown because a person added it.">
      would be excluded: {reason}
    </span>
  );
}

export default function ManualTenderPanel({ tender, onDeleted }: { tender: Tender; onDeleted: () => void }) {
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteManualTender(tender.pub_number);
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div style={{ marginBottom: 16, padding: 10, background: 'rgba(192,132,252,0.06)', border: '1px solid rgba(192,132,252,0.25)', borderRadius: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <ManualBadge />
        <WouldBeExcluded reason={tender.exclude_reason} />
        <span style={{ fontSize: 12, color: '#8892a4' }}>
          Entered by {tender.created_by || 'unknown'}{tender.created_at ? ` on ${formatDate(tender.created_at)}` : ''}
        </span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }}
                  onClick={() => navigate(`/scout/add-tender?edit=${encodeURIComponent(tender.pub_number)}`)}>
            Edit
          </button>
          {!confirming ? (
            <button className="btn btn-red" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => setConfirming(true)}>
              Delete
            </button>
          ) : (
            <>
              <button className="btn btn-red" style={{ fontSize: 12, padding: '4px 10px' }} disabled={busy} onClick={remove}>
                {busy ? 'Deleting…' : 'Confirm delete'}
              </button>
              <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} disabled={busy}
                      onClick={() => setConfirming(false)}>
                Keep
              </button>
            </>
          )}
        </div>
      </div>
      {error && <div style={{ color: '#f87171', fontSize: 12, marginTop: 6 }}>{error}</div>}
    </div>
  );
}
