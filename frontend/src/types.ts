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
}

export interface CpvConfigEntry {
  code: string;
  labels: { en: string | null; fr: string | null; nl: string | null; de: string | null };
  group: string | null;
  category: string | null;
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

// ── Vault ─────────────────────────────────────────────────────────────────────

export interface VaultDoc {
  id: string;
  filename: string;
  doc_type: 'Datasheet' | 'Drawing' | 'Certificate' | string;
  status: 'indexed' | 'processing';
  metadata: Record<string, string>;
  cpv_codes: string[];
  confidence: number;
  fields_extracted: number;
}

// ── Composer ──────────────────────────────────────────────────────────────────

export interface ComposerRequirement {
  id: string;
  title: string;
  extracted: string;
  source: string;
  confidence: number;
  validation: 'pending' | 'validated' | 'flagged';
  similarity?: number;
  response?: string;
  citations?: { doc: string; score: number }[];
  gap_note?: string;
  gap_status?: 'complete' | 'linked' | 'completed';
}

export interface ComposerDoc {
  id: string;
  filename: string;
  role: 'sow' | 'tech' | 'background' | 'parta' | 'example';
  pages: number;
  chunks: number;
  status: 'ingested' | 'pending' | 'style_only';
  image_heavy?: boolean;
}

export interface ComposerSession {
  pub_number: string;
  tender_title: string;
  source: string;
  deadline: string;
  docs: ComposerDoc[];
  requirements: ComposerRequirement[];
}
