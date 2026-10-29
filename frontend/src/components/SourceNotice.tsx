import { SOURCE_NOTICES } from '../utils';

// Attribution + disclaimer for a notice whose source requires them (see
// utils.ts SOURCE_NOTICES); renders nothing for every other source.
export default function SourceNotice({ source }: { source: string }) {
  const notice = SOURCE_NOTICES[source];
  if (!notice) return null;
  return (
    <div style={{ background: '#151d2c', border: '1px solid #1a2334', borderRadius: 6, padding: '8px 12px', marginBottom: 12, fontSize: 11, color: '#8892a4' }}>
      <div style={{ color: '#e2e8f0', fontWeight: 500, marginBottom: 2 }}>{notice.attribution}</div>
      <div>{notice.disclaimer}</div>
    </div>
  );
}
