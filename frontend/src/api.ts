import type { Tender, TenderListResponse, Stats, PortalHealth, PipelineEntry, FollowupEntry, PipelineHistoryEntry, DocumentEntry, VaultDoc, VaultSearchResponse, VaultRules, VaultSettings, ComposerSession, ComposerDoc, ComposerMatrix, CpvConfigEntry, KeywordsConfig, SettingsConfig, ComposerSettings, ComposerStyleGuide, ComposerStyleExample } from './types';
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
  notice_type?: string;
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
  if (filters.notice_type) params.set('notice_type', filters.notice_type);
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  if (filters.offset !== undefined) params.set('offset', String(filters.offset));
  if (filters.sort) params.set('sort', filters.sort);
  const qs = params.toString();
  return apiFetch<TenderListResponse>(`/api/tenders${qs ? '?' + qs : ''}`);
}

export function getTender(pub_number: string): Promise<Tender> {
  return apiFetch<Tender>(`/api/tenders/${encodeURIComponent(pub_number)}`);
}

export function patchTender(pub_number: string, status: string, note?: string): Promise<unknown> {
  return apiFetch(`/api/tenders/${encodeURIComponent(pub_number)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(note ? { status, note } : { status }),
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

export function getPipelineHistory(pub_number: string): Promise<{ pub_number: string; history: PipelineHistoryEntry[] }> {
  return apiFetch(`/api/pipeline/${encodeURIComponent(pub_number)}/history`);
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

// ── Documents (CR-002 E, shortlisted tenders only) ───────────────────────────

export function listDocuments(pub_number: string): Promise<DocumentEntry[]> {
  return apiFetch<DocumentEntry[]>(`/api/tenders/${encodeURIComponent(pub_number)}/documents`);
}

export function uploadDocument(pub_number: string, file: File): Promise<DocumentEntry> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<DocumentEntry>(`/api/tenders/${encodeURIComponent(pub_number)}/documents`, {
    method: 'POST',
    body: form,
  });
}

export async function downloadDocumentBlob(id: number): Promise<Blob> {
  const token = await getAuthToken();
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const r = await fetch(`${BASE}/api/documents/${id}`, { headers });
  if (!r.ok) throw new Error(`${r.status} /api/documents/${id}`);
  return r.blob();
}

// ── CPV config ────────────────────────────────────────────────────────────────

export function getCpvConfig(): Promise<CpvConfigEntry[]> {
  return apiFetch<CpvConfigEntry[]>('/api/config/cpv');
}

export interface PutCpvResult {
  saved: boolean;
  warnings: string[];
}

export function putCpvConfig(codes: string[]): Promise<PutCpvResult> {
  return apiFetch<PutCpvResult>('/api/config/cpv', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ codes }),
  });
}

// ── Keywords config ───────────────────────────────────────────────────────────

export function getKeywordsConfig(): Promise<KeywordsConfig> {
  return apiFetch<KeywordsConfig>('/api/config/keywords');
}

export interface PutKeywordsBody {
  terms?: Record<string, string[]>;
  distinctive?: string[];
}

export function putKeywordsConfig(body: PutKeywordsBody): Promise<{ saved: boolean }> {
  return apiFetch('/api/config/keywords', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── Settings config ───────────────────────────────────────────────────────────

export function getSettingsConfig(): Promise<SettingsConfig> {
  return apiFetch<SettingsConfig>('/api/config/settings');
}

export function putSettingsConfig(body: Partial<SettingsConfig>): Promise<{ saved: boolean }> {
  return apiFetch('/api/config/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// ── Reports ───────────────────────────────────────────────────────────────────

export class ReportNotFoundError extends Error {}

export async function getLatestReportBlob(): Promise<Blob> {
  const token = await getAuthToken();
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const r = await fetch(BASE + '/api/reports/latest', { headers });
  if (r.status === 404) throw new ReportNotFoundError('No report found');
  if (!r.ok) throw new Error(`${r.status} /api/reports/latest`);
  return r.blob();
}

// ── Vault ─────────────────────────────────────────────────────────────────────

export function listVaultDocs(q?: string, tag?: string): Promise<{ total: number; processing: number; results: VaultDoc[] }> {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  if (tag) params.set('tag', tag);
  const qs = params.toString();
  return apiFetch(`/api/vault/docs${qs ? '?' + qs : ''}`);
}

export function uploadVaultDoc(file: File): Promise<VaultDoc> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<VaultDoc>('/api/vault/ingest', { method: 'POST', body: form });
}

export function setVaultDocTags(id: number, tags: string[]): Promise<{ id: number; tags: string[] }> {
  return apiFetch(`/api/vault/docs/${id}/tags`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags }),
  });
}

export function listVaultTags(): Promise<{ tags: string[] }> {
  return apiFetch('/api/vault/tags');
}

// ── Vault Rules / Settings ───────────────────────────────────────────────────

export function getVaultRules(): Promise<VaultRules> {
  return apiFetch('/api/vault/rules');
}

export function putVaultRules(hints: string[]): Promise<{ saved: boolean }> {
  return apiFetch('/api/vault/rules', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hints }),
  });
}

export function getVaultSettings(): Promise<VaultSettings> {
  return apiFetch('/api/vault/settings');
}

export function putVaultSettings(body: Partial<VaultSettings>): Promise<{ saved: boolean }> {
  return apiFetch('/api/vault/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

// CR-004 F3 — Composer's "Source materials" panel: search the Vault library
// by CPV code / material type, optionally ranked by similarity to a query.
export function searchVault(opts: { query?: string; cpv?: string; material?: string }): Promise<VaultSearchResponse> {
  const params = new URLSearchParams();
  if (opts.query) params.set('query', opts.query);
  if (opts.cpv) params.set('cpv', opts.cpv);
  if (opts.material) params.set('material', opts.material);
  const qs = params.toString() ? `?${params.toString()}` : '';
  return apiFetch(`/api/vault/search${qs}`);
}

// ── Composer ──────────────────────────────────────────────────────────────────

export function getComposerSession(pub?: string): Promise<ComposerSession | null> {
  if (!pub) return Promise.resolve(null);
  return apiFetch<ComposerSession>(`/api/composer/session/${encodeURIComponent(pub)}`).catch(() => null);
}

export function uploadComposerDocument(pub: string, file: File, role?: string): Promise<ComposerDoc> {
  const form = new FormData();
  form.append('file', file);
  if (role) form.append('role', role);
  return apiFetch<ComposerDoc>(`/api/composer/${encodeURIComponent(pub)}/documents`, {
    method: 'POST', body: form,
  });
}

export function updateComposerDocumentRole(pub: string, docId: number, role: string): Promise<unknown> {
  return apiFetch(`/api/composer/${encodeURIComponent(pub)}/documents/${docId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role }),
  });
}

export function uploadComposerMatrix(pub: string, file: File): Promise<ComposerMatrix> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch<ComposerMatrix>(`/api/composer/${encodeURIComponent(pub)}/matrix`, {
    method: 'POST', body: form,
  });
}

export function triggerComposerEnrich(pub: string): Promise<{ status: string }> {
  return apiFetch(`/api/composer/${encodeURIComponent(pub)}/enrich`, { method: 'POST' });
}

export function triggerComposerInterpret(pub: string): Promise<{ status: string }> {
  return apiFetch(`/api/composer/${encodeURIComponent(pub)}/interpret`, { method: 'POST' });
}

export function patchRequirement(id: number, status: 'pending' | 'validated' | 'flagged'): Promise<unknown> {
  return apiFetch(`/api/composer/requirements/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  });
}

export function resolveComposerRequirement(id: number): Promise<unknown> {
  return apiFetch(`/api/composer/requirements/${id}/resolve`, { method: 'POST' });
}

export function postGenerate(pub: string): Promise<unknown> {
  return apiFetch(`/api/composer/${encodeURIComponent(pub)}/generate`, { method: 'POST' });
}

export function regenerateComposerSection(
  pub: string, requirementId: number, feedback: string, vaultDocumentIds?: number[],
): Promise<unknown> {
  return apiFetch(`/api/composer/${encodeURIComponent(pub)}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      requirement_id: requirementId, feedback,
      ...(vaultDocumentIds && vaultDocumentIds.length ? { vault_document_ids: vaultDocumentIds } : {}),
    }),
  });
}

async function _downloadComposerBlob(pub: string, filename: string): Promise<Blob> {
  const token = await getAuthToken();
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const path = `/api/composer/${encodeURIComponent(pub)}/download/${filename}`;
  const r = await fetch(BASE + path, { headers });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.blob();
}

export function downloadComposerProposalBlob(pub: string): Promise<Blob> {
  return _downloadComposerBlob(pub, 'proposal.docx');
}

export function downloadComposerMatrixBlob(pub: string): Promise<Blob> {
  return _downloadComposerBlob(pub, 'matrix.xlsx');
}

export function downloadComposerGapsBlob(pub: string): Promise<Blob> {
  return _downloadComposerBlob(pub, 'gaps_report.txt');
}

// ── Composer Settings / Style Guide (tenant-wide, not tender-scoped) ────────

export function getComposerSettings(): Promise<ComposerSettings> {
  return apiFetch('/api/composer/settings');
}

export function putComposerSettings(body: Partial<ComposerSettings>): Promise<{ saved: boolean }> {
  return apiFetch('/api/composer/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function getComposerStyle(): Promise<ComposerStyleGuide> {
  return apiFetch('/api/composer/style');
}

export function putComposerStyle(style_guide: string): Promise<ComposerStyleGuide> {
  return apiFetch('/api/composer/style', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ style_guide }),
  });
}

export function listStyleExamples(): Promise<{ results: ComposerStyleExample[] }> {
  return apiFetch('/api/composer/style/examples');
}

export function uploadStyleExample(file: File): Promise<{ id: number; filename: string }> {
  const form = new FormData();
  form.append('file', file);
  return apiFetch('/api/composer/style/examples', { method: 'POST', body: form });
}

export function deleteStyleExample(id: number): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/composer/style/examples/${id}`, { method: 'DELETE' });
}

export function extractComposerStyle(): Promise<ComposerStyleGuide> {
  return apiFetch('/api/composer/style/extract', { method: 'POST' });
}
