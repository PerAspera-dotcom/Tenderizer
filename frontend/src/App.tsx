import { useEffect } from 'react';
import { SignedIn, SignedOut, SignIn, useAuth } from '@clerk/clerk-react';
import { HashRouter, Navigate, useLocation } from './router';
import { setTokenGetter } from './authToken';
import Layout from './components/Layout';
import PortalCalendar from './pages/portal/PortalCalendar';
import PortalPipeline from './pages/portal/PortalPipeline';
import PortalFollowup from './pages/portal/PortalFollowup';
import Dashboard from './pages/scout/Dashboard';
import TenderFeed from './pages/scout/TenderFeed';
import PastTenders from './pages/scout/PastTenders';
import ReviewQueue from './pages/scout/ReviewQueue';
import PortalsHealth from './pages/scout/PortalsHealth';
import MatchingConfig from './pages/scout/MatchingConfig';
import Settings from './pages/scout/Settings';
import VaultLibrary from './pages/vault/VaultLibrary';
import ComposerIngest from './pages/composer/ComposerIngest';
import ProposalReview from './pages/composer/ProposalReview';
import GapsReport from './pages/composer/GapsReport';
import StubPage from './pages/StubPage';

function Router() {
  const { pathname } = useLocation();

  const routes: Record<string, React.ReactNode> = {
    '/portal/calendar': <Layout><PortalCalendar /></Layout>,
    '/portal/pipeline': <Layout><PortalPipeline /></Layout>,
    '/portal/followup': <Layout><PortalFollowup /></Layout>,
    '/scout/dashboard': <Layout><Dashboard /></Layout>,
    '/scout/tender-feed': <Layout><TenderFeed /></Layout>,
    '/scout/past-tenders': <Layout><PastTenders /></Layout>,
    '/scout/review-queue': <Layout><ReviewQueue /></Layout>,
    '/scout/portals': <Layout><PortalsHealth /></Layout>,
    '/scout/matching': <Layout><MatchingConfig /></Layout>,
    '/scout/settings': <Layout><Settings /></Layout>,
    '/vault/library': <Layout><VaultLibrary /></Layout>,
    '/vault/rules': <Layout><StubPage title="Metadata Rules" subtitle="Define extraction rules for Vault documents" /></Layout>,
    '/vault/collections': <Layout><StubPage title="Collections" subtitle="Group and tag your document library" /></Layout>,
    '/vault/settings': <Layout><StubPage title="Vault Settings" subtitle="Extraction model, confidence thresholds" /></Layout>,
    '/composer/ingest': <Layout><ComposerIngest /></Layout>,
    '/composer/review': <Layout><ProposalReview /></Layout>,
    '/composer/gaps': <Layout><GapsReport /></Layout>,
    '/composer/style': <Layout><StubPage title="Style Guide" subtitle="House style extracted from example proposals" /></Layout>,
    '/composer/settings': <Layout><StubPage title="Composer Settings" subtitle="API key, thresholds, model" /></Layout>,
  };

  // CR-002 F/D-D: Calendar is now the Portal's default landing screen.
  if (pathname === '/' || pathname === '/portal') return <Navigate to="/portal/calendar" />;
  if (pathname === '/vault') return <Navigate to="/vault/library" />;
  if (pathname === '/composer') return <Navigate to="/composer/ingest" />;
  if (!routes[pathname]) return <Navigate to="/portal/calendar" />;

  return <>{routes[pathname]}</>;
}

// Bridges Clerk's getToken (only available via hook, inside the provider
// tree) out to api.ts's plain async functions — see authToken.ts.
function TokenBridge() {
  const { getToken } = useAuth();
  useEffect(() => {
    setTokenGetter(getToken);
    return () => setTokenGetter(null);
  }, [getToken]);
  return null;
}

export default function App() {
  return (
    <>
      <SignedIn>
        <TokenBridge />
        <HashRouter>
          <Router />
        </HashRouter>
      </SignedIn>
      <SignedOut>
        <div style={{ display: 'flex', height: '100vh', alignItems: 'center', justifyContent: 'center', background: '#0f1623' }}>
          <SignIn />
        </div>
      </SignedOut>
    </>
  );
}
