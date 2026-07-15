/**
 * Minimal hash-based router (no react-router-dom needed).
 * Uses window.location.hash: route is everything after '#'.
 * e.g. http://localhost:5173/#/portal/home
 */
import { createContext, useContext, useState, useEffect, type ReactNode, type MouseEvent } from 'react';

interface RouterCtx {
  path: string;
  search: string;
  navigate: (to: string) => void;
}

const Ctx = createContext<RouterCtx>({ path: '/', search: '', navigate: () => {} });

function hashToPath(): { path: string; search: string } {
  const hash = window.location.hash.replace(/^#/, '') || '/';
  const idx = hash.indexOf('?');
  if (idx === -1) return { path: hash, search: '' };
  return { path: hash.slice(0, idx), search: hash.slice(idx) };
}

export function HashRouter({ children }: { children: ReactNode }) {
  const [loc, setLoc] = useState(hashToPath);

  useEffect(() => {
    const handler = () => setLoc(hashToPath());
    window.addEventListener('hashchange', handler);
    // If no hash yet, default to /portal/calendar (CR-002 F/D-D)
    if (!window.location.hash) {
      window.location.hash = '#/portal/calendar';
    }
    return () => window.removeEventListener('hashchange', handler);
  }, []);

  function navigate(to: string) {
    window.location.hash = '#' + to;
  }

  return <Ctx.Provider value={{ path: loc.path, search: loc.search, navigate }}>{children}</Ctx.Provider>;
}

export function useNavigate() {
  return useContext(Ctx).navigate;
}

export function useLocation() {
  const { path, search } = useContext(Ctx);
  return { pathname: path, search };
}

export function useSearchParams(): [URLSearchParams, (p: URLSearchParams) => void] {
  const { search, navigate } = useContext(Ctx);
  const { path } = useContext(Ctx);
  const params = new URLSearchParams(search);
  function setParams(p: URLSearchParams) {
    const qs = p.toString();
    navigate(path + (qs ? '?' + qs : ''));
  }
  return [params, setParams];
}

export function Link({ to, children, style, className, onClick }: {
  to: string; children: ReactNode; style?: React.CSSProperties; className?: string;
  onClick?: () => void;
}) {
  const { navigate } = useContext(Ctx);
  function handleClick(e: MouseEvent<HTMLAnchorElement>) {
    e.preventDefault();
    onClick?.();
    navigate(to);
  }
  return (
    <a href={'#' + to} onClick={handleClick} style={style} className={className}>
      {children}
    </a>
  );
}

interface RouteConfig {
  path: string;
  element: ReactNode;
  redirect?: string;
}

function matchRoute(pattern: string, actual: string): boolean {
  if (pattern === actual) return true;
  if (pattern.endsWith('*')) return actual.startsWith(pattern.slice(0, -1));
  return false;
}

export function Routes({ routes }: { routes: RouteConfig[] }) {
  const { path, navigate } = useContext(Ctx);

  for (const r of routes) {
    if (matchRoute(r.path, path)) {
      if (r.redirect) {
        // Redirect immediately
        setTimeout(() => navigate(r.redirect!), 0);
        return null;
      }
      return <>{r.element}</>;
    }
  }
  // Default: redirect to home
  setTimeout(() => navigate('/portal/home'), 0);
  return null;
}

export function Navigate({ to }: { to: string }) {
  const { navigate } = useContext(Ctx);
  useEffect(() => { navigate(to); }, []);
  return null;
}
