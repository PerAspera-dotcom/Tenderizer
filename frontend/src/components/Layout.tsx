import { useState, useEffect, useRef, type ReactNode } from 'react';
import { UserButton } from '@clerk/clerk-react';
import { useNavigate, useLocation, Link } from '../router';
import { getStats, getHealth, postRun, listTenders } from '../api';
import type { Stats, PortalHealth } from '../types';
import { formatTime } from '../utils';

type AppId = 'portal' | 'scout' | 'vault' | 'composer';

const VAULT_NAV: NavItem[] = [
  { label: 'Library', path: '/vault/library' },
  { label: 'Metadata Rules', path: '/vault/rules' },
  { label: 'Collections', path: '/vault/collections' },
  { label: 'Settings', path: '/vault/settings' },
];

const COMPOSER_NAV: NavItem[] = [
  { label: 'Ingest & Config', path: '/composer/ingest' },
  { label: 'Proposal Review', path: '/composer/review' },
  { label: 'Gaps Report', path: '/composer/gaps' },
  { label: 'Style Guide', path: '/composer/style' },
  { label: 'Settings', path: '/composer/settings' },
];

interface NavItem {
  label: string;
  path: string;
  badgeKey?: string;
  groupStart?: string; // renders a small uppercase group label above this item
}

const PORTAL_NAV: NavItem[] = [
  // CR-002 F/D-D: Calendar is the first thing you see when opening Portal —
  // and its own deadline-alerts strip now covers what the old separate
  // "Home" launchpad page showed (its other content, the Scout/Vault/
  // Composer launch cards, duplicated the app-switcher one click away).
  { label: 'Calendar', path: '/portal/calendar' },
  { label: 'Pipeline & Deadlines', path: '/portal/pipeline' },
  { label: 'Follow-up & Results', path: '/portal/followup' },
];

// Grouped so a first-time user can tell "things I do" from "things I
// configure once" at a glance, rather than nine flat, undifferentiated links.
const SCOUT_NAV: NavItem[] = [
  { label: 'Dashboard', path: '/scout/dashboard', groupStart: 'Workflow' },
  { label: 'Tender Feed', path: '/scout/tender-feed' },
  { label: 'Past Tenders', path: '/scout/past-tenders' },
  { label: 'Review Queue', path: '/scout/review-queue', badgeKey: 'newCount' },
  { label: 'Portals & Health', path: '/scout/portals', groupStart: 'Configuration' },
  { label: 'Matching Config', path: '/scout/matching' },
  { label: 'Settings', path: '/scout/settings' },
];

const STUB_PATHS = [
  '/vault/rules', '/vault/collections', '/vault/settings',
  '/composer/style', '/composer/settings',
];

const APP_INFO: Record<AppId, { name: string; subtitle: string; badge: string; color: string }> = {
  portal: { name: 'Portal', subtitle: 'Pipeline, deadlines & foll…', badge: 'PORTAL', color: '#2EE6D4' },
  scout: { name: 'Scout', subtitle: 'Monitor & match tenders', badge: 'SCOUT', color: '#2EE6D4' },
  vault: { name: 'Vault', subtitle: 'Technical document library', badge: 'VAULT', color: '#60a5fa' },
  composer: { name: 'Composer', subtitle: 'Draft tender proposals', badge: 'COMPOSER', color: '#c084fc' },
};

function appAccent(app: AppId): string {
  if (app === 'vault') return '#60a5fa';
  if (app === 'composer') return '#c084fc';
  return '#2EE6D4';
}

function currentApp(path: string): AppId {
  if (path.startsWith('/scout')) return 'scout';
  if (path.startsWith('/vault')) return 'vault';
  if (path.startsWith('/composer')) return 'composer';
  return 'portal';
}

interface Props {
  children: ReactNode;
}

export default function Layout({ children }: Props) {
  const location = useLocation();
  const navigate = useNavigate();
  const app = currentApp(location.pathname);
  const appInfo = APP_INFO[app];

  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [stats, setStats] = useState<Stats | null>(null);
  const [health, setHealth] = useState<PortalHealth[]>([]);
  const [newCount, setNewCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [searchVal, setSearchVal] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getStats().then(setStats).catch(() => {});
    getHealth().then(setHealth).catch(() => {});
    listTenders({ status: 'new', limit: 1000 }).then(r => setNewCount(r.total)).catch(() => {});
  }, [location.pathname]);

  const engineLive = health.some(h => h.status === 'live');

  function handleRun() {
    setRunning(true);
    postRun().catch(() => {}).finally(() => {
      let count = 0;
      pollRef.current = setInterval(() => {
        count++;
        getStats().then(s => { setStats(s); }).catch(() => {});
        if (count >= 10) {
          clearInterval(pollRef.current!);
          setRunning(false);
        }
      }, 3000);
    });
  }

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (searchVal.trim()) navigate(`/scout/tender-feed?q=${encodeURIComponent(searchVal.trim())}`);
  }

  const nav = app === 'scout' ? SCOUT_NAV : app === 'portal' ? PORTAL_NAV : app === 'vault' ? VAULT_NAV : app === 'composer' ? COMPOSER_NAV : [];

  const syncLabel = stats?.last_sync
    ? `Synced ${formatTime(stats.last_sync)}`
    : 'Not synced';

  return (
    <div style={{ display: 'flex', height: '100%', background: '#0f1623' }}>
      {/* Sidebar */}
      <aside style={{ width: 230, minWidth: 230, background: '#0d1320', borderRight: '1px solid #1a2334', display: 'flex', flexDirection: 'column', height: '100%', overflowY: 'auto' }}>
        {/* Logo + app badge */}
        <div style={{ padding: '16px 16px 0', display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 32, height: 32, background: '#2EE6D4', borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M11 2L5 10h4l-2 6 7-9h-4l2-5z" fill="#0f1623" strokeLinejoin="round" />
            </svg>
          </div>
          <span style={{ fontWeight: 700, fontSize: 16, color: '#e2e8f0' }}>Tenderizer</span>
          <span style={{ marginLeft: 'auto', background: 'rgba(255,255,255,0.08)', color: '#8892a4', fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', padding: '2px 6px', borderRadius: 4 }}>
            {appInfo.badge}
          </span>
        </div>

        {/* App switcher header */}
        <button
          onClick={() => setSwitcherOpen(o => !o)}
          style={{ margin: '12px 10px 0', background: 'rgba(255,255,255,0.04)', border: '1px solid #1a2334', borderRadius: 8, padding: '10px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 10, color: '#e2e8f0', textAlign: 'left' }}
        >
          <div style={{ width: 28, height: 28, background: '#1a2334', borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            {app === 'portal' && <span style={{ fontSize: 14 }}>🏠</span>}
            {app === 'scout' && <span style={{ fontSize: 14, color: '#2EE6D4' }}>◎</span>}
            {app === 'vault' && <span style={{ fontSize: 14, color: '#60a5fa' }}>📁</span>}
            {app === 'composer' && <span style={{ fontSize: 14, color: '#c084fc' }}>✏️</span>}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{appInfo.name}</div>
            <div style={{ color: '#8892a4', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{appInfo.subtitle}</div>
          </div>
          <span style={{ color: '#8892a4', fontSize: 14 }}>{switcherOpen ? '⌃' : '⌄'}</span>
        </button>

        {/* App switcher panel */}
        {switcherOpen && (
          <div style={{ margin: '4px 10px', background: '#151d2c', border: '1px solid #1a2334', borderRadius: 8, overflow: 'hidden' }}>
            {([
              { id: 'portal', label: 'Portal', icon: '🏠', sub: 'Pipeline & deadlines', path: '/portal/calendar', badge: null },
              { id: 'scout', label: 'Scout', icon: '◎', sub: 'Monitor & match tenders', path: '/scout/dashboard', badge: null, iconColor: '#2EE6D4' },
              { id: 'vault', label: 'Vault', icon: '📁', sub: 'Technical documents', path: '/vault', badge: 'DEV', iconColor: '#60a5fa' },
              { id: 'composer', label: 'Composer', icon: '✏️', sub: 'Draft proposals', path: '/composer', badge: 'DEV', iconColor: '#c084fc' },
            ] as const).map(item => (
              <Link
                key={item.id}
                to={item.path}
                onClick={() => setSwitcherOpen(false)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px',
                  borderBottom: '1px solid #1a2334',
                  background: app === item.id ? 'rgba(46,230,212,0.06)' : 'transparent',
                  color: '#e2e8f0',
                }}
              >
                <span style={{ fontSize: 16, color: (item as { iconColor?: string }).iconColor }}>{item.icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 500, fontSize: 13 }}>{item.label}</div>
                  <div style={{ color: '#8892a4', fontSize: 11 }}>{item.sub}</div>
                </div>
                {item.badge && (
                  <span style={{ background: 'rgba(227,179,65,0.15)', color: '#e3b341', border: '1px solid rgba(227,179,65,0.3)', fontSize: 10, fontWeight: 700, padding: '1px 5px', borderRadius: 4 }}>
                    {item.badge}
                  </span>
                )}
              </Link>
            ))}
          </div>
        )}

        {/* Menu label */}
        {nav.length > 0 && (
          <div style={{ padding: '16px 16px 4px', fontSize: 10, fontWeight: 700, letterSpacing: '0.1em', color: '#4c5a70', textTransform: 'uppercase' }}>
            {app === 'portal' ? 'Portal Menu' : app === 'scout' ? 'Scout Menu' : app === 'vault' ? 'Vault Menu' : 'Composer Menu'}
          </div>
        )}

        {/* Nav items */}
        <nav style={{ flex: 1, padding: '0 8px' }}>
          {nav.map(item => {
            const isActive = location.pathname === item.path;
            const isStub = STUB_PATHS.includes(item.path);
            const accent = appAccent(app);
            const accentBg = app === 'vault' ? 'rgba(96,165,250,0.08)' : app === 'composer' ? 'rgba(192,132,252,0.08)' : 'rgba(46,230,212,0.08)';
            return (
              <div key={item.path}>
                {item.groupStart && (
                  <div style={{
                    padding: '10px 10px 4px', fontSize: 9.5, fontWeight: 700, letterSpacing: '0.09em',
                    color: '#3a4658', textTransform: 'uppercase',
                  }}>
                    {item.groupStart}
                  </div>
                )}
                <Link
                  to={item.path}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', borderRadius: 6,
                    marginBottom: 2, color: isActive ? accent : '#8892a4',
                    background: isActive ? accentBg : 'transparent',
                    fontSize: 13, fontWeight: isActive ? 600 : 400,
                    borderLeft: isActive ? `2px solid ${accent}` : '2px solid transparent',
                    textDecoration: 'none',
                  }}
                >
                  <span style={{ flex: 1 }}>{item.label}</span>
                  {item.badgeKey === 'newCount' && newCount > 0 && (
                    <span style={{ background: appAccent(app), color: '#0f1623', fontSize: 11, fontWeight: 700, padding: '1px 6px', borderRadius: 9999 }}>
                      {newCount}
                    </span>
                  )}
                  {isStub && (
                    <span style={{ fontSize: 12, opacity: 0.6 }}>🚧</span>
                  )}
                </Link>
              </div>
            );
          })}
        </nav>

        {/* Engine status pill */}
        <div style={{ padding: '12px 12px 16px' }}>
          <div style={{ background: '#151d2c', border: '1px solid #1a2334', borderRadius: 20, padding: '7px 12px', display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
            <span className={engineLive ? 'dot-live' : 'dot-paused'} />
            <span style={{ color: '#8892a4' }}>Scout engine{' '}
              <strong style={{ color: engineLive ? '#2EE6D4' : '#e3b341' }}>{engineLive ? 'online' : 'offline'}</strong>
            </span>
          </div>
        </div>
      </aside>

      {/* Main content area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, overflow: 'hidden' }}>
        {/* Top bar */}
        <header style={{ height: 56, background: '#0d1320', borderBottom: '1px solid #1a2334', display: 'flex', alignItems: 'center', padding: '0 20px', gap: 16, flexShrink: 0 }}>
          <form onSubmit={handleSearch} style={{ flex: 1, maxWidth: 380 }}>
            <div style={{ position: 'relative' }}>
              <span style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: '#4c5a70', fontSize: 14 }}>⌕</span>
              <input
                className="input-field"
                style={{ paddingLeft: 30 }}
                placeholder="Search tenders, CPV codes, buyers…"
                value={searchVal}
                onChange={e => setSearchVal(e.target.value)}
              />
            </div>
          </form>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, background: '#151d2c', border: '1px solid #1a2334', borderRadius: 20, padding: '5px 12px', fontSize: 13 }}>
              <span className="dot-live" />
              <span style={{ color: '#e2e8f0' }}>{syncLabel}</span>
            </div>
            {/* Scout-only: triggering the scrape engine has no meaning from
                inside Portal/Vault/Composer, and Scout's own Dashboard
                already carries this same action in its last-sync strip —
                showing it globally on every app was pure duplication. */}
            {app === 'scout' && (
              <button
                className="btn btn-teal"
                onClick={handleRun}
                disabled={running}
                style={{ opacity: running ? 0.7 : 1 }}
              >
                {running ? '⟳' : '↺'} {running ? 'Running…' : 'Run now'}
              </button>
            )}
            <UserButton appearance={{ elements: { avatarBox: { width: 32, height: 32 } } }} />
          </div>
        </header>

        {/* Page content */}
        <main style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
          {children}
        </main>
      </div>
    </div>
  );
}
