import Stub from '../components/Stub';

interface Props {
  title: string;
  subtitle?: string;
}

export default function StubPage({ title, subtitle }: Props) {
  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>{title}</h1>
      {subtitle && <p style={{ color: '#8892a4', marginBottom: 28 }}>{subtitle}</p>}
      <Stub title={title} subtitle={subtitle} />
    </div>
  );
}
