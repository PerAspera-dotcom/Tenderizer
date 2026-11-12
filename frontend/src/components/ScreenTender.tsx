import { useState } from 'react';
import { screenTender } from '../api';
import type { ScreeningAnswer } from '../types';

// CR-015 §3.4: screening runs when a person asks for it, for one tender —
// one Claude call, made through the backend's gateway (feature switch and
// monthly ceiling apply). The server's reason is shown when it says no.
export default function ScreenTender({ pubNumber, hasAnswers, onAnswers }: {
  pubNumber: string;
  hasAnswers: boolean;
  onAnswers: (answers: ScreeningAnswer[]) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setMessage(null);
    try {
      const r = await screenTender(pubNumber);
      onAnswers(r.screening_answers);
      if (r.answered === 0) setMessage('Every enabled question already has an answer.');
    } catch (e) {
      setMessage(e instanceof Error ? e.message : 'Screening failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <button
        onClick={run}
        disabled={busy}
        style={{ background: 'transparent', border: '1px solid #2a3548', color: busy ? '#5a6680' : '#60a5fa', borderRadius: 6, padding: '4px 10px', fontSize: 12, cursor: busy ? 'default' : 'pointer' }}
      >
        {busy ? 'Screening…' : hasAnswers ? 'Answer missing screening questions' : 'Screen this tender'}
      </button>
      {message && <span style={{ fontSize: 12, color: '#8892a4' }}>{message}</span>}
    </div>
  );
}
