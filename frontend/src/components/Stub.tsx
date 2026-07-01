interface Props {
  title: string;
  subtitle?: string;
}

export default function Stub({ title, subtitle }: Props) {
  return (
    <div style={{ position: 'relative', minHeight: '400px', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
      {/* Diagonal stripe overlay */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        backgroundImage: 'repeating-linear-gradient(45deg, rgba(227,179,65,0.04) 0px, rgba(227,179,65,0.04) 1px, transparent 1px, transparent 16px)',
        borderRadius: 10,
      }} />
      {/* 🚧 badge */}
      <div style={{ position: 'absolute', top: 16, right: 16, background: 'rgba(227,179,65,0.15)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', borderRadius: 6, padding: '4px 10px', fontSize: 13, fontWeight: 600 }}>
        🚧 Coming soon
      </div>
      <div className="card" style={{ padding: '40px 56px', textAlign: 'center', position: 'relative', zIndex: 1 }}>
        <div style={{ fontSize: 40, marginBottom: 16 }}>🚧</div>
        <h2 style={{ fontSize: 22, fontWeight: 700, marginBottom: 8, color: '#e2e8f0' }}>{title}</h2>
        {subtitle && <p style={{ color: '#8892a4', marginBottom: 16 }}>{subtitle}</p>}
        <p style={{ color: '#e3b341', fontWeight: 500 }}>Coming soon — in active development</p>
      </div>
    </div>
  );
}
