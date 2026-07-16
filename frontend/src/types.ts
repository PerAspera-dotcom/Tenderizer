export interface Tender {
  hash: string;
  source: string;
  pub_number: string;
  tag_line: string;
  description: string;
  buyer: string;
  country: string;
  place: string;
  category: string;
  procedure: string;
  pub_date: string;
  deadline: string;
  cpv_codes: string[];
  matched_terms: string[];
  match_source: string;
  url: string;
  first_seen: string;
  status: string;
  language: string;
  tag_line_en: string;
  description_en: string;
  translation_status: string;
  value: string;
  value_currency: string;
  dismiss_note: string | null;
  notice_type: string;
  awarded_to: string | null;
  awarded_value: string | null;
  awarded_currency: string | null;
}

export interface TenderListResponse {
  total: number;
  results: Tender[];
}

export interface Stats {
  last_sync: string | null;
  next_run: string | null;
  notices_scanned: number;
  matched_total: number;
  new_today: number;
  by_match: { cpv: number; both: number; keyword: number; none: number };
  by_category: Record<string, number>;
  portals_active: string;
  past_tenders: number;
}

export interface CpvConfigEntry {
  code: string;
  labels: { en: string | null; fr: string | null; nl: string | null; de: string | null };
  group: string | null;
  category: string | null;
}

export interface KeywordsConfig {
  terms: Record<string, string[]>;
  distinctive: string[];
}

export interface SettingsConfig {
  run_frequency: 'daily' | 'weekly' | 'paused' | string;
  run_window_start: string;
  run_window_end: string;
  notify_on_complete: boolean;
  notify_email: string;
}

export interface PortalHealth {
  name: string;
  region: string;
  status: string;
  last_result?: string;
  detail?: string;
}

export interface PipelineEntry extends Tender {
  submission_status: string;
  deadline_override: string | null;
  owner: string | null;
  notes: string | null;
}

export interface FollowupEntry extends PipelineEntry {
  submitted_date: string | null;
  result_due: string | null;
  outcome: string;
}

// CR-002 E: minimal document upload slice (shortlisted tenders only)
export interface DocumentEntry {
  id: number;
  filename: string;
  content_type: string;
  size: number;
  uploaded_at: string;
}

// ── Vault ─────────────────────────────────────────────────────────────────────

export interface VaultDoc {
  id: number;
  filename: string;
  doc_type: 'Datasheet' | 'Drawing' | 'Certificate' | string | null;
  status: 'indexed' | 'processing';
  metadata: Record<string, string>;
  cpv_codes: string[];
  confidence: number | null;
  fields_extracted: number | null;
}

// ── Composer ──────────────────────────────────────────────────────────────────

export interface ComposerRequirement {
  id: number;
  title: string;
  extracted: string;
  source: string;
  confidence: number | null;
  validation: 'pending' | 'validated' | 'flagged';
  gap_status: 'complete' | 'linked' | 'completed' | null;
  similarity: number | null;
  response: string | null;
  citations: { doc: string; score: number }[];
  resolved: boolean;
  version: number;
  version_history: { text: string | null; feedback: string; at: string }[];
}

export interface ComposerDoc {
  id: number;
  filename: string;
  role: 'sow' | 'tech' | 'background' | 'parta' | 'example' | 'unknown';
  status: 'ingested' | 'processing' | 'style_only';
  pages: number | null;
  chunks: number | null;
  image_heavy: boolean;
}

export interface ComposerMatrix {
  filename: string;
  requirement_count: number;
  filled: boolean;
}

export interface ComposerSession {
  pub_number: string;
  tender_title: string;
  source: string;
  deadline: string;
  docs: ComposerDoc[];
  matrix: ComposerMatrix | null;
  requirements: ComposerRequirement[];
}
