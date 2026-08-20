import { useEffect, useState } from 'react';
import { forwardTender, getOrgMembers, type OrgMember } from '../api';

// CR-007 Phase G (G1): "forwarding a request based on sender/recipient
// accounts" — a deadline reminder from the Pipeline, or a "please review
// this" nudge from the Review Queue. Shared between both since it's the
// same action (an explicit account-to-account email) either way.
//
// Post-CR-007: the recipient picker is the real Clerk org roster (self
// excluded) when one is available — falls back to manual email entry for a
// solo tenant, a caller with no active org, or CLERK_SECRET_KEY unset
// server-side (getOrgMembers just returns [] in all of those cases; see
// api.get_org_members).
const MANUAL_ENTRY = '__manual__';

interface Props {
  pubNumber: string;
}

export default function ForwardTender({ pubNumber }: Props) {
  const [open, setOpen] = useState(false);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [selected, setSelected] = useState('');
  const [toEmail, setToEmail] = useState('');
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    getOrgMembers().then(setMembers).catch(() => setMembers([]));
  }, [open]);

  function close() {
    setOpen(false);
    setSelected('');
    setToEmail('');
    setMessage('');
    setError('');
    setSent(false);
  }

  const manualEntry = members.length === 0 || selected === MANUAL_ENTRY;
  const recipient = (manualEntry ? toEmail : selected).trim();

  async function send() {
    if (!recipient.includes('@')) return;
    setSending(true);
    setError('');
    try {
      await forwardTender(pubNumber, recipient, message.trim() || undefined);
      setSent(true);
      setTimeout(close, 1500);
    } catch {
      setError('Failed to send — try again');
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button className="btn btn-ghost" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => setOpen(true)}>
        ✉ Forward
      </button>
    );
  }

  return (
    <div style={{ marginTop: 12, padding: 12, background: 'rgba(96,165,250,0.05)', border: '1px solid rgba(96,165,250,0.2)', borderRadius: 8 }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 8 }}>
        Forward to a colleague
      </div>
      {!manualEntry && (
        <select
          className="input-field" style={{ width: '100%', marginBottom: 6, fontSize: 12 }}
          value={selected} onChange={e => setSelected(e.target.value)}
        >
          <option value="">Select a colleague…</option>
          {members.map(m => <option key={m.clerk_user_id} value={m.email}>{m.email}</option>)}
          <option value={MANUAL_ENTRY}>Other (enter email)…</option>
        </select>
      )}
      {manualEntry && (
        <>
          <input
            className="input-field" style={{ width: '100%', marginBottom: 6, fontSize: 12 }}
            placeholder="colleague@example.com"
            value={toEmail} onChange={e => setToEmail(e.target.value)}
          />
          {members.length > 0 && (
            <button className="btn btn-ghost" style={{ fontSize: 11, padding: '2px 6px', marginBottom: 6 }}
                    onClick={() => { setSelected(''); setToEmail(''); }}>
              ← Pick from colleagues
            </button>
          )}
        </>
      )}
      <textarea
        className="input-field" style={{ width: '100%', minHeight: 50, fontSize: 12, resize: 'vertical' }}
        placeholder="Add a note (optional)…"
        value={message} onChange={e => setMessage(e.target.value)}
      />
      <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
        <button className="btn btn-blue" style={{ fontSize: 12 }}
                disabled={sending || !recipient.includes('@')} onClick={send}>
          {sending ? 'Sending…' : 'Send'}
        </button>
        <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={close} disabled={sending}>Cancel</button>
        {sent && <span style={{ color: '#34d399', fontSize: 12 }}>Sent</span>}
        {error && <span style={{ color: '#f87171', fontSize: 12 }}>{error}</span>}
      </div>
    </div>
  );
}
