// CR-002 C1: type badge for eoi/fbo/prequalification — plain/no badge for
// the default "tender" type. Labelled "PIN" for notice_type="fbo": the
// customer's original ask ("FBO") is a legacy US federal-procurement term;
// what's actually detected/shown is TED/BOAMP's real forward-looking notice
// type, Prior Information Notice (CR-002 D-B).
const LABELS: Record<string, string> = {
  eoi: 'EOI',
  fbo: 'PIN',
  prequalification: 'Prequalification',
};

const COLORS: Record<string, string> = {
  eoi: '#60a5fa',
  fbo: '#c084fc',
  prequalification: '#e3b341',
};

interface Props {
  noticeType: string | null | undefined;
}

export default function NoticeTypeBadge({ noticeType }: Props) {
  if (!noticeType || noticeType === 'tender' || noticeType === 'past_tender') return null;
  const label = LABELS[noticeType] ?? noticeType;
  const color = COLORS[noticeType] ?? '#8892a4';
  return (
    <span style={{
      background: `${color}1a`, color, border: `1px solid ${color}4d`,
      borderRadius: 4, padding: '2px 7px', fontSize: 11, fontWeight: 600,
    }}>
      {label}
    </span>
  );
}
