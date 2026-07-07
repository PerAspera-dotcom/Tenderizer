import { useEffect, useState } from 'react';
import { getStats, getLatestReportBlob, ReportNotFoundError } from '../../api';
import type { Stats } from '../../types';
import { formatDate, formatTime } from '../../utils';

export default function Reports() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getStats().then(setStats).catch(() => {}).finally(() => setLoading(false));
  }, []);

  async function handleExport() {
    setExporting(true);
    setError('');
    try {
      const blob = await getLatestReportBlob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'tenders.xlsx';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof ReportNotFoundError
        ? 'No report yet — run the pipeline from the Dashboard first.'
        : 'Failed to download the report — try again.');
    } finally {
      setExporting(false);
    }
  }

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Reports</h1>
      <p style={{ color: '#8892a4', marginBottom: 20 }}>
        Export your latest run as an Excel workbook, categorised by match confidence and category
      </p>

      <div className="card" style={{ padding: '20px 24px', maxWidth: 480 }}>
        <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase', marginBottom: 10 }}>
          Latest run
        </div>
        {loading ? (
          <div style={{ color: '#8892a4', fontSize: 13, marginBottom: 16 }}>Loading…</div>
        ) : (
          <div style={{ fontSize: 13, color: '#8892a4', marginBottom: 16 }}>
            {stats?.last_sync
              ? <>{formatDate(stats.last_sync)} at {formatTime(stats.last_sync)} · {stats.notices_scanned} scanned · {stats.matched_total} matched</>
              : 'No run recorded yet'}
          </div>
        )}

        <button className="btn btn-teal" onClick={handleExport} disabled={exporting}>
          {exporting ? 'Preparing…' : '⤓ Export to Excel'}
        </button>
        {error && <div style={{ color: '#f87171', fontSize: 13, marginTop: 12 }}>{error}</div>}
      </div>
    </div>
  );
}
