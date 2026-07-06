import type { Tender, TenderListResponse, Stats, PortalHealth, PipelineEntry, FollowupEntry, VaultDoc, ComposerSession } from './types';
import { getAuthToken } from './authToken';

const BASE = (import.meta.env.VITE_API_BASE as string) ?? 'http://localhost:8000';

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getAuthToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const r = await fetch(BASE + path, { ...init, headers });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json() as Promise<T>;
}

export interface TenderFilters {
  source?: string;
  category?: string;
  match_source?: string;
  country?: string;
  q?: string;
  status?: string;
  has_deadline?: boolean;
  limit?: number;
  offset?: number;
  sort?: string;
}

export function listTenders(filters: TenderFilters = {}): Promise<TenderListResponse> {
  const params = new URLSearchParams();
  if (filters.source) params.set('source', filters.source);
  if (filters.category) params.set('category', filters.category);
  if (filters.match_source) params.set('match_source', filters.match_source);
  if (filters.country) params.set('country', filters.country);
  if (filters.q) params.set('q', filters.q);
  if (filters.status) params.set('status', filters.status);
  if (filters.has_deadline !== undefined) params.set('has_deadline', String(filters.has_deadline));
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  if (filters.offset !== undefined) params.set('offset', String(filters.offset));
  if (filters.sort) params.set('sort', filters.sort);
  const qs = params.toString();
  return apiFetch<TenderListResponse>(`/api/tenders${qs ? '?' + qs : ''}`);
}

export function getTender(pub_number: string): Promise<Tender> {
  return apiFetch<Tender>(`/api/tenders/${encodeURIComponent(pub_number)}`);
}

export function patchTender(pub_number: string, status: string): Promise<unknown> {
  return apiFetch(`/api/tenders/${encodeURIComponent(pub_number)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
}

export function getStats(): Promise<Stats> {
  return apiFetch<Stats>('/api/stats');
}

export function getHealth(): Promise<PortalHealth[]> {
  return apiFetch<PortalHealth[]>('/api/health');
}

export function postRun(): Promise<{ status: string }> {
  return apiFetch('/api/run', { method: 'POST' });
}

export function getPipeline(): Promise<PipelineEntry[]> {
  return apiFetch<PipelineEntry[]>('/api/pipeline');
}

export interface PipelinePatch {
  submission_status?: string;
  deadline_override?: string;
  notes?: string;
  owner?: string;
}

export function patchPipeline(pub_number: string, body: PipelinePatch): Promise<unknown> {
  return apiFetch(`/api/pipeline/${encodeURIComponent(pub_number)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function getFollowup(): Promise<FollowupEntry[]> {
  return apiFetch<FollowupEntry[]>('/api/followup');
}

export function patchFollowup(pub_number: string, outcome: string): Promise<unknown> {
  return apiFetch(`/api/followup/${encodeURIComponent(pub_number)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ outcome }),
  });
}

// ── Vault ─────────────────────────────────────────────────────────────────────

export function listVaultDocs(q?: string): Promise<{ total: number; processing: number; results: VaultDoc[] }> {
  const qs = q ? `?q=${encodeURIComponent(q)}` : '';
  return apiFetch(`/api/vault/docs${qs}`);
}

// ── Composer ──────────────────────────────────────────────────────────────────

export function getComposerSession(pub?: string): Promise<ComposerSession | null> {
  const path = pub
    ? `/api/composer/session/${encodeURIComponent(pub)}`
    : '/api/composer/session';
  return apiFetch<ComposerSession>(path).catch(() => null);
}

export function patchRequirement(id: string, status: 'validated' | 'flagged'): Promise<unknown> {
  return apiFetch(`/api/composer/requirements/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
}

export function postGenerate(pub: string): Promise<unknown> {
  return apiFetch(`/api/composer/${encodeURIComponent(pub)}/generate`, { method: 'POST' });
}
