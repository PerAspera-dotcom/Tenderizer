import { matchLabel } from '../utils';

interface Props {
  matchSource: string | null | undefined;
}

export default function MatchChip({ matchSource }: Props) {
  const label = matchLabel(matchSource);
  const cls =
    label === 'Both' ? 'chip-both' :
    label === 'CPV' ? 'chip-cpv' :
    label === 'Keyword' ? 'chip-keyword' : 'chip-none';
  return <span className={cls}>• {label}</span>;
}
