import { useState } from 'react';
import { forwardTender } from '../api';

// CR-007 Phase G (G1): "forwarding a request based on sender/recipient
// accounts" — a deadline reminder from the Pipeline, or a "please review
// this" nudge from the Review Queue. Shared between both since it's the
// same action (an explicit account-to-account email) either way.
interface Props {
  pubNumber: string;
}

export default function ForwardTender({ pubNumber }: Props) {
  const [open, setOpen] = useState(false);
  const [toEmail, setToEmail] = useState('');
  const [message, setMessage] = useState('');
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');

  function close() {
    setOpen(false);
    setToEmail('');
    setMessage('');
    setError('');
    setSent(false);
  }

  async function send() {
    const trimmed = toEmail.trim();
    if (!trimmed.includes('@')) return;
    setSending(true);
    setError('');
    try {
      await forwardTender(pubNumber, trimmed, message.trim() || undefined);
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
      <input
        className="input-field" style={{ width: '100%', marginBottom: 6, fontSize: 12 }}
        placeholder="colleague@example.com"
        value={toEmail} onChange={e => setToEmail(e.target.value)}
      />
      <textarea
        className="input-field" style={{ width: '100%', minHeight: 50, fontSize: 12, resize: 'vertical' }}
        placeholder="Add a note (optional)…"
        value={message} onChange={e => setMessage(e.target.value)}
      />
      <div style={{ display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' }}>
        <button className="btn btn-blue" style={{ fontSize: 12 }}
                disabled={sending || !toEmail.trim().includes('@')} onClick={send}>
          {sending ? 'Sending…' : 'Send'}
        </button>
        <button className="btn btn-ghost" style={{ fontSize: 12 }} onClick={close} disabled={sending}>Cancel</button>
        {sent && <span style={{ color: '#34d399', fontSize: 12 }}>Sent</span>}
        {error && <span style={{ color: '#f87171', fontSize: 12 }}>{error}</span>}
      </div>
    </div>
  );
}
