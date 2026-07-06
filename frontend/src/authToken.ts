// Lets api.ts (plain async functions, not hooks) attach the current Clerk
// session token without every caller threading it through by hand. The
// ClerkProvider tree registers its getToken via setTokenGetter once on mount.
type TokenGetter = () => Promise<string | null>;

let getter: TokenGetter | null = null;

export function setTokenGetter(fn: TokenGetter | null) {
  getter = fn;
}

export function getAuthToken(): Promise<string | null> {
  return getter ? getter() : Promise.resolve(null);
}
