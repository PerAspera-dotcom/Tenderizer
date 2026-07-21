import { useState } from 'react';
import CpvConfig from './CpvConfig';
import KeywordsConfig from './KeywordsConfig';

type Tab = 'cpv' | 'keywords';

// CR-004/5 UX pass: CPV Config and Keywords were two separate nav items for
// one concept — both feed the exact same match_source logic (CPV vs
// keyword vs both). One nav item, two tabs, same underlying screens.
export default function MatchingConfig() {
  const [tab, setTab] = useState<Tab>('cpv');

  const tabs: { key: Tab; label: string }[] = [
    { key: 'cpv', label: 'CPV Codes' },
    { key: 'keywords', label: 'Keywords' },
  ];

  return (
    <div>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Matching Config</h1>
      <p style={{ color: '#8892a4', marginBottom: 16 }}>
        The two signals that decide whether a scraped notice reaches your Tender Feed
      </p>

      <div style={{ display: 'flex', gap: 6, marginBottom: 20 }}>
        {tabs.map(t => {
          const isActive = tab === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                padding: '7px 16px', borderRadius: 6, fontSize: 13, fontWeight: 500, cursor: 'pointer',
                border: `1px solid ${isActive ? '#2EE6D4' : '#1a2334'}`,
                background: isActive ? 'rgba(46,230,212,0.12)' : 'transparent',
                color: isActive ? '#2EE6D4' : '#8892a4',
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'cpv' ? <CpvConfig /> : <KeywordsConfig />}
    </div>
  );
}
