import { useEffect, useState } from 'react';
import { getScreeningQuestions, addScreeningQuestion, updateScreeningQuestion } from '../../api';
import type { ScreeningQuestion } from '../../types';

// CR-011 §3: per-tenant configurable screening questions — answered from
// notice text at ingest (src/screening.py) and cached per tender
// (tender_screening_answers). Editing a question's text invalidates its
// cached answers (store.update_screening_question deletes them on text
// change), so they're recomputed on the next pipeline run — no separate
// "recompute" button needed here.
export default function ScreeningQuestions() {
  const [questions, setQuestions] = useState<ScreeningQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [newText, setNewText] = useState('');
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState('');

  function load(): Promise<ScreeningQuestion[]> {
    return getScreeningQuestions().then(data => {
      const sorted = [...data.questions].sort((a, b) => a.display_order - b.display_order);
      setQuestions(sorted);
      return sorted;
    });
  }

  useEffect(() => {
    load().catch(() => setError('Failed to load screening questions')).finally(() => setLoading(false));
  }, []);

  async function handleAdd(e: React.FormEvent) {
    e.preventDefault();
    const text = newText.trim();
    if (!text) return;
    setAdding(true);
    setError('');
    try {
      await addScreeningQuestion(text);
      await load();
      setNewText('');
    } catch {
      setError('Failed to add — try again');
    } finally {
      setAdding(false);
    }
  }

  async function toggleEnabled(q: ScreeningQuestion) {
    setBusyId(q.id);
    setError('');
    try {
      await updateScreeningQuestion(q.id, { enabled: !q.enabled });
      setQuestions(questions.map(x => x.id === q.id ? { ...x, enabled: !x.enabled } : x));
    } catch {
      setError('Failed to save — try again');
    } finally {
      setBusyId(null);
    }
  }

  function startEdit(q: ScreeningQuestion) {
    setEditingId(q.id);
    setEditText(q.question_text);
  }

  async function saveEdit(q: ScreeningQuestion) {
    const text = editText.trim();
    if (!text || text === q.question_text) {
      setEditingId(null);
      return;
    }
    setBusyId(q.id);
    setError('');
    try {
      await updateScreeningQuestion(q.id, { question_text: text });
      setQuestions(questions.map(x => x.id === q.id ? { ...x, question_text: text } : x));
      setEditingId(null);
    } catch {
      setError('Failed to save — try again');
    } finally {
      setBusyId(null);
    }
  }

  async function move(index: number, direction: -1 | 1) {
    const other = index + direction;
    if (other < 0 || other >= questions.length) return;
    const a = questions[index];
    const b = questions[other];
    setBusyId(a.id);
    setError('');
    try {
      await Promise.all([
        updateScreeningQuestion(a.id, { display_order: b.display_order }),
        updateScreeningQuestion(b.id, { display_order: a.display_order }),
      ]);
      const reordered = [...questions];
      reordered[index] = { ...b, display_order: a.display_order };
      reordered[other] = { ...a, display_order: b.display_order };
      setQuestions(reordered);
    } catch {
      setError('Failed to reorder — try again');
    } finally {
      setBusyId(null);
    }
  }

  if (loading) return <div className="loading">Loading…</div>;

  return (
    <div>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        Questions answered automatically from each notice's text at ingest — shown with source
        snippets on the Review Queue, or "Not stated" when the notice doesn't say. Disabling a
        question stops new answers from being computed for it; past answers stay cached.
      </p>

      <form onSubmit={handleAdd} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 16 }}>
        <div style={{ flex: '0 1 420px' }}>
          <input
            className="input-field"
            placeholder="+ Add a screening question…"
            value={newText}
            onChange={ev => setNewText(ev.target.value)}
            disabled={adding}
          />
        </div>
        <button type="submit" className="btn btn-teal" disabled={adding || !newText.trim()}>
          {adding ? 'Adding…' : '+ Add question'}
        </button>
      </form>
      {error && <div style={{ color: '#f87171', fontSize: 13, marginBottom: 12 }}>{error}</div>}

      <div className="card">
        <div style={{ padding: '10px 16px', borderBottom: '1px solid #1a2334', color: '#8892a4', fontSize: 12 }}>
          {questions.length} question{questions.length === 1 ? '' : 's'}
        </div>
        {questions.length === 0 ? (
          <div style={{ padding: 32, textAlign: 'center', color: '#8892a4' }}>No screening questions configured yet.</div>
        ) : (
          questions.map((q, i) => (
            <div
              key={q.id}
              style={{
                display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px',
                borderBottom: i < questions.length - 1 ? '1px solid #1a2334' : 'none',
                opacity: busyId === q.id ? 0.6 : 1,
              }}
            >
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                <button
                  title="Move up"
                  onClick={() => move(i, -1)}
                  disabled={i === 0 || busyId !== null}
                  style={{ background: 'transparent', border: 'none', color: '#8892a4', cursor: i === 0 ? 'default' : 'pointer', fontSize: 11, lineHeight: 1, padding: 2 }}
                >
                  ▲
                </button>
                <button
                  title="Move down"
                  onClick={() => move(i, 1)}
                  disabled={i === questions.length - 1 || busyId !== null}
                  style={{ background: 'transparent', border: 'none', color: '#8892a4', cursor: i === questions.length - 1 ? 'default' : 'pointer', fontSize: 11, lineHeight: 1, padding: 2 }}
                >
                  ▼
                </button>
              </div>

              <div style={{ flex: 1, minWidth: 0 }}>
                {editingId === q.id ? (
                  <input
                    className="input-field"
                    autoFocus
                    value={editText}
                    onChange={e => setEditText(e.target.value)}
                    onBlur={() => saveEdit(q)}
                    onKeyDown={e => { if (e.key === 'Enter') saveEdit(q); if (e.key === 'Escape') setEditingId(null); }}
                    style={{ width: '100%' }}
                  />
                ) : (
                  <span
                    onClick={() => startEdit(q)}
                    style={{ fontSize: 13, color: q.enabled ? '#cdd6e3' : '#5a6680', cursor: 'pointer' }}
                    title="Click to edit"
                  >
                    {q.question_text}
                  </span>
                )}
              </div>

              <button
                role="switch"
                aria-checked={q.enabled}
                title={q.enabled ? 'Enabled — click to disable' : 'Disabled — click to enable'}
                onClick={() => toggleEnabled(q)}
                disabled={busyId === q.id}
                style={{
                  width: 36, height: 20, borderRadius: 9999, border: 'none', position: 'relative', flexShrink: 0,
                  background: q.enabled ? '#2EE6D4' : '#2a3448',
                }}
              >
                <span style={{
                  position: 'absolute', top: 2, left: q.enabled ? 18 : 2, width: 16, height: 16, borderRadius: '50%',
                  background: '#0f1623', transition: 'left 0.1s',
                }} />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
