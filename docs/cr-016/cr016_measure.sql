-- CR-016 measurement block. Read-only: a single SELECT, no writes.
-- Paste the whole thing into the Railway Postgres console and run it once.
-- Output: one result set of (s, o, section, metric, value) rows, ordered.
WITH
t AS (   -- every non-deleted tender, with parsed helpers
  SELECT tn.*,
         CASE WHEN left(tn.first_seen,10) ~ '^\d{4}-\d{2}-\d{2}$' THEN left(tn.first_seen,10)::date END AS fs,
         CASE WHEN tn.value_eur ~ '^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$' THEN tn.value_eur::numeric END AS veur,
         (tn.exclude_reason = '' OR tn.entry_method = 'manual') AS visible,
         (tn.deadline = '' OR left(tn.deadline,10) >= to_char(current_date,'YYYY-MM-DD')) AS not_expired,
         COALESCE(NULLIF(tn.match_source,''),'none') AS ms
  FROM tenders tn
  WHERE tn.deleted_at IS NULL
),
surf AS (  -- what the Review Queue can show (visible, not a past_tender)
  SELECT * FROM t WHERE visible AND notice_type <> 'past_tender'
),
dec AS (   -- first status change away from 'new' per tender (tender_history)
  SELECT DISTINCT ON (h.tenant_id, h.pub_number)
         h.tenant_id, h.pub_number, h.new_value,
         CASE WHEN left(h.changed_at,10) ~ '^\d{4}-\d{2}-\d{2}$' THEN left(h.changed_at,10)::date END AS d
  FROM tender_history h
  WHERE h.field = 'status' AND COALESCE(h.new_value,'') <> 'new'
  ORDER BY h.tenant_id, h.pub_number, h.changed_at
),
lat AS (   -- decision latency in days
  SELECT t.source, t.language, dec.new_value, (dec.d - t.fs) AS days
  FROM dec JOIN t ON t.tenant_id = dec.tenant_id AND t.pub_number = dec.pub_number
  WHERE dec.d IS NOT NULL AND t.fs IS NOT NULL
),
kw AS (    -- matched terms on surfaced keyword/both tenders
  SELECT s.status, s.ms, term
  FROM surf s, LATERAL jsonb_array_elements_text(
         CASE WHEN s.matched_terms ~ '^\s*\[' THEN s.matched_terms::jsonb ELSE '[]'::jsonb END) AS term
),
dupt AS (  -- tenders that carry at least one duplicate link
  SELECT tenant_id, pub_number_a AS pub FROM tender_duplicates
  UNION SELECT tenant_id, pub_number_b FROM tender_duplicates
),
r AS (
-- ================= 01 OVERVIEW =================
SELECT 1 s, 0::bigint o, 'overview' section, 'measured_at' metric, now()::text value
UNION ALL SELECT 1,1,'overview','tenants', count(*)::text FROM tenants
UNION ALL SELECT 1,2,'overview','tenders_total_incl_deleted', count(*)::text FROM tenders
UNION ALL SELECT 1,3,'overview','tenders_soft_deleted', count(*)::text FROM tenders WHERE deleted_at IS NOT NULL
UNION ALL SELECT 1,4,'overview','tenders_per_tenant: tenant '||tenant_id, count(*)::text FROM t GROUP BY tenant_id
UNION ALL SELECT 1,5,'overview','first_seen min / max', min(fs)::text||' / '||max(fs)::text FROM t
UNION ALL SELECT 1,6,'overview','first_seen unparseable', count(*)::text FROM t WHERE fs IS NULL

-- ================= 02 VOLUME & FLOW =================
UNION ALL SELECT 2,0,'status (all)', status, count(*)::text FROM t GROUP BY status
UNION ALL SELECT 2,1,'status (surfaced)', status, count(*)::text FROM surf GROUP BY status
UNION ALL SELECT 2,2,'status x tenant (surfaced)', 'tenant '||tenant_id||' / '||status, count(*)::text FROM surf GROUP BY tenant_id, status
UNION ALL SELECT 2,3,'source (all | surfaced | shortlisted | dismissed*)', source,
       count(*)::text||' | '||count(*) FILTER (WHERE visible AND notice_type<>'past_tender')::text
       ||' | '||count(*) FILTER (WHERE visible AND status='shortlisted')::text
       ||' | '||count(*) FILTER (WHERE visible AND status IN ('dismissed','dismissed_final'))::text
  FROM t GROUP BY source
UNION ALL SELECT 2,4,'notice_type (all | surfaced)', notice_type,
       count(*)::text||' | '||count(*) FILTER (WHERE visible)::text FROM t GROUP BY notice_type
UNION ALL SELECT 2,5,'entry_method', entry_method, count(*)::text FROM t GROUP BY entry_method
UNION ALL SELECT 2,6,'arrivals/week (all | surfaced)', to_char(date_trunc('week',fs),'YYYY-MM-DD'),
       count(*)::text||' | '||count(*) FILTER (WHERE visible AND notice_type<>'past_tender')::text
  FROM t WHERE fs IS NOT NULL GROUP BY date_trunc('week',fs)
UNION ALL SELECT 2,7,'left the queue (surfaced, status<>new)', 'count', count(*)::text FROM surf WHERE status <> 'new'
UNION ALL SELECT 2,8,'reached pipeline', 'pipeline rows', count(*)::text FROM pipeline
UNION ALL SELECT 2,9,'pipeline submission_status', submission_status, count(*)::text FROM pipeline GROUP BY submission_status
UNION ALL SELECT 2,10,'pipeline outcome', outcome, count(*)::text FROM pipeline GROUP BY outcome
UNION ALL SELECT 2,11,'backlog (surfaced, new|needs_review, not expired)', 'count', count(*)::text
  FROM surf WHERE status IN ('new','needs_review') AND not_expired
UNION ALL SELECT 2,12,'backlog (surfaced, new|needs_review, not expired)', 'oldest first_seen', min(fs)::text
  FROM surf WHERE status IN ('new','needs_review') AND not_expired
UNION ALL SELECT 2,13,'backlog age buckets (days since first_seen)',
       CASE WHEN current_date-fs <= 7 THEN 'a 0-7' WHEN current_date-fs <= 30 THEN 'b 8-30'
            WHEN current_date-fs <= 90 THEN 'c 31-90' ELSE 'd 90+' END, count(*)::text
  FROM surf WHERE status IN ('new','needs_review') AND not_expired AND fs IS NOT NULL GROUP BY 4
UNION ALL SELECT 2,14,'surfaced but expired, never decided', 'count', count(*)::text
  FROM surf WHERE status IN ('new','needs_review') AND NOT not_expired
UNION ALL SELECT 2,15,'default queue load (rows GET /api/tenders returns, no filter)', 'tenant '||tenant_id, count(*)::text
  FROM surf WHERE (not_expired OR status='dismissed_final') GROUP BY tenant_id
UNION ALL SELECT 2,16,'deadline empty (surfaced)', 'count', count(*)::text FROM surf WHERE deadline = ''

-- ================= 03 FILTERS =================
UNION ALL SELECT 3,0,'exclude_reason (all)', COALESCE(NULLIF(exclude_reason,''),'(kept)'), count(*)::text FROM t GROUP BY exclude_reason
UNION ALL SELECT 3,1,'exclude_reason x source', source||' / '||exclude_reason, count(*)::text FROM t WHERE exclude_reason<>'' GROUP BY source, exclude_reason
UNION ALL SELECT 3,2,'match_source (all | surfaced)', ms, count(*)::text||' | '||count(*) FILTER (WHERE visible)::text FROM t GROUP BY ms
UNION ALL SELECT 3,3,'match_source x source (surfaced)', source||' / '||ms, count(*)::text FROM surf GROUP BY source, ms
UNION ALL SELECT 3,4,'match_source outcome (surfaced: total | shortlisted | dismissed* | reviewed | needs_review | new)', ms,
       count(*)::text||' | '||count(*) FILTER (WHERE status='shortlisted')::text
       ||' | '||count(*) FILTER (WHERE status IN ('dismissed','dismissed_final'))::text
       ||' | '||count(*) FILTER (WHERE status='reviewed')::text
       ||' | '||count(*) FILTER (WHERE status='needs_review')::text
       ||' | '||count(*) FILTER (WHERE status='new')::text
  FROM surf GROUP BY ms
UNION ALL SELECT 3,5,'keyword-only surfaced x language', COALESCE(NULLIF(language,''),'(blank)'), count(*)::text FROM surf WHERE ms='keyword' GROUP BY language
UNION ALL SELECT 3,6,'distinct matched terms ever seen on surfaced', 'count', count(DISTINCT term)::text FROM kw
UNION ALL SELECT 3,7,'top matched terms on surfaced (hits | shortlisted | dismissed*)', term,
       count(*)::text||' | '||count(*) FILTER (WHERE status='shortlisted')::text
       ||' | '||count(*) FILTER (WHERE status IN ('dismissed','dismissed_final'))::text
  FROM kw GROUP BY term HAVING count(*) >= 3
UNION ALL SELECT 3,8,'terms on keyword-ONLY surfaced (hits | shortlisted)', term,
       count(*)::text||' | '||count(*) FILTER (WHERE status='shortlisted')::text
  FROM kw WHERE ms='keyword' GROUP BY term
UNION ALL SELECT 3,9,'surfaced decided: dismissal rate', 'dismissed* / decided',
       count(*) FILTER (WHERE status IN ('dismissed','dismissed_final'))::text||' / '||count(*) FILTER (WHERE status<>'new')::text
  FROM surf
UNION ALL SELECT 3,10,'reason_category (dismissed*)', COALESCE(reason_category,'(null)'), count(*)::text
  FROM t WHERE status IN ('dismissed','dismissed_final') GROUP BY reason_category
UNION ALL SELECT * FROM (
  SELECT 3 AS s, row_number() OVER (ORDER BY count(*) DESC)+100 AS o, 'top dismissal_reason texts' AS section, left(dismissal_reason,120) AS metric, count(*)::text AS value
  FROM t WHERE dismissal_reason IS NOT NULL GROUP BY left(dismissal_reason,120)) x WHERE o <= 125

-- ================= 04 VALUE FLOOR =================
UNION ALL SELECT 4,0,'value_eur present (all | surfaced)', 'count',
       count(*) FILTER (WHERE veur IS NOT NULL)::text||' | '||count(*) FILTER (WHERE veur IS NOT NULL AND visible)::text FROM t
UNION ALL SELECT 4,1,'value_eur present by source (all | with value)', source, count(*)::text||' | '||count(veur)::text FROM t GROUP BY source
UNION ALL SELECT 4,2,'value_eur buckets (all | below_floor-excluded | surfaced | shortlisted | dismissed* | value_too_low)',
       CASE WHEN veur < 25000 THEN 'a <25k' WHEN veur < 50000 THEN 'b 25-50k' WHEN veur < 100000 THEN 'c 50-100k'
            WHEN veur < 200000 THEN 'd 100-200k' WHEN veur < 500000 THEN 'e 200-500k' WHEN veur < 1000000 THEN 'f 0.5-1M'
            WHEN veur < 5000000 THEN 'g 1-5M' ELSE 'h 5M+' END,
       count(*)::text||' | '||count(*) FILTER (WHERE exclude_reason='below_value_floor')::text
       ||' | '||count(*) FILTER (WHERE visible AND notice_type<>'past_tender')::text
       ||' | '||count(*) FILTER (WHERE visible AND status='shortlisted')::text
       ||' | '||count(*) FILTER (WHERE visible AND status IN ('dismissed','dismissed_final'))::text
       ||' | '||count(*) FILTER (WHERE reason_category='value_too_low')::text
  FROM t WHERE veur IS NOT NULL GROUP BY 4
UNION ALL SELECT 4,3,'below_value_floor: max value_eur by source (implied floor)', source, round(max(veur))::text
  FROM t WHERE exclude_reason='below_value_floor' GROUP BY source
UNION ALL SELECT 4,4,'kept with value: min value_eur by source (implied floor)', source, round(min(veur))::text
  FROM t WHERE exclude_reason='' AND entry_method<>'manual' AND veur IS NOT NULL GROUP BY source
UNION ALL SELECT 4,5,'shortlisted value_eur percentiles p10/p25/p50', 'eur',
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY veur))::text||' / '||round(percentile_cont(0.25) WITHIN GROUP (ORDER BY veur))::text
       ||' / '||round(percentile_cont(0.5) WITHIN GROUP (ORDER BY veur))::text
  FROM t WHERE status='shortlisted' AND veur IS NOT NULL

-- ================= 05 REVIEW QUEUE =================
UNION ALL SELECT 5,0,'tender_history status rows', 'count / first / last',
       count(*)::text||' / '||min(left(changed_at,10))||' / '||max(left(changed_at,10)) FROM tender_history WHERE field='status'
UNION ALL SELECT 5,1,'tender_history fields', field, count(*)::text FROM tender_history GROUP BY field
UNION ALL SELECT 5,2,'status transitions old->new', COALESCE(old_value,'(null)')||' -> '||COALESCE(new_value,'(null)'), count(*)::text
  FROM tender_history WHERE field='status' GROUP BY old_value, new_value
UNION ALL SELECT 5,3,'decided tenders lacking history (surfaced, status<>new)', 'count', count(*)::text
  FROM surf s WHERE status<>'new' AND NOT EXISTS (SELECT 1 FROM tender_history h WHERE h.tenant_id=s.tenant_id AND h.pub_number=s.pub_number AND h.field='status')
UNION ALL SELECT 5,4,'days first_seen -> first decision: n / p10 / p25 / p50 / p75 / p90 / max', 'all',
       count(*)::text||' / '||percentile_cont(0.1) WITHIN GROUP (ORDER BY days)::text||' / '||percentile_cont(0.25) WITHIN GROUP (ORDER BY days)::text
       ||' / '||percentile_cont(0.5) WITHIN GROUP (ORDER BY days)::text||' / '||percentile_cont(0.75) WITHIN GROUP (ORDER BY days)::text
       ||' / '||percentile_cont(0.9) WITHIN GROUP (ORDER BY days)::text||' / '||max(days)::text FROM lat
UNION ALL SELECT 5,5,'days first_seen -> first decision buckets',
       CASE WHEN days <= 0 THEN 'a same day' WHEN days <= 2 THEN 'b 1-2' WHEN days <= 7 THEN 'c 3-7'
            WHEN days <= 30 THEN 'd 8-30' ELSE 'e 30+' END, count(*)::text FROM lat GROUP BY 4
UNION ALL SELECT 5,6,'decision latency p50 by first decision', new_value, count(*)::text||' / p50 '||percentile_cont(0.5) WITHIN GROUP (ORDER BY days)::text FROM lat GROUP BY new_value
UNION ALL SELECT 5,7,'decision latency p50 by source', source, count(*)::text||' / p50 '||percentile_cont(0.5) WITHIN GROUP (ORDER BY days)::text FROM lat GROUP BY source
UNION ALL SELECT 5,8,'decisions per weekday', to_char(d,'ID Dy'), count(*)::text FROM dec WHERE d IS NOT NULL GROUP BY 4
UNION ALL SELECT 5,9,'decisions per week', to_char(date_trunc('week',d),'YYYY-MM-DD'), count(*)::text FROM dec WHERE d IS NOT NULL GROUP BY 4
UNION ALL SELECT 5,10,'dismissed_by (dismissed*)', COALESCE(split_part(dismissed_by,'@',1),'(null)'), count(*)::text
  FROM t WHERE status IN ('dismissed','dismissed_final') GROUP BY 4
UNION ALL SELECT 5,11,'decisions by language (surfaced: total | shortlisted | dismissed* | new)', COALESCE(NULLIF(language,''),'(blank)'),
       count(*)::text||' | '||count(*) FILTER (WHERE status='shortlisted')::text
       ||' | '||count(*) FILTER (WHERE status IN ('dismissed','dismissed_final'))::text||' | '||count(*) FILTER (WHERE status='new')::text
  FROM surf GROUP BY language
UNION ALL SELECT 5,12,'translation_status (surfaced)', COALESCE(NULLIF(translation_status,''),'(blank)'), count(*)::text FROM surf GROUP BY translation_status
UNION ALL SELECT 5,13,'assigned_to set (needs_review)', 'count', count(*)::text FROM t WHERE assigned_to IS NOT NULL
UNION ALL SELECT 5,14,'relevance overrides', 'rows / distinct accounts', count(*)::text||' / '||count(DISTINCT account_name)::text FROM tender_relevance_overrides
UNION ALL SELECT 5,15,'presence heartbeats', 'rows / distinct users / last seen', count(*)::text||' / '||count(DISTINCT clerk_user_id)::text||' / '||COALESCE(max(left(last_seen_at,10)),'-') FROM tender_presence
UNION ALL SELECT 5,16,'notifications by kind (total | read)', kind, count(*)::text||' | '||count(read_at)::text FROM tender_notifications GROUP BY kind
UNION ALL SELECT 5,17,'supersedes: tenders superseding others', 'count', count(*)::text FROM t WHERE supersedes NOT IN ('[]','')

-- ================= 06 DUPLICATES =================
UNION ALL SELECT 6,0,'duplicate pairs by match_type (pairs | resolved)', match_type, count(*)::text||' | '||count(resolved_by)::text FROM tender_duplicates GROUP BY match_type
UNION ALL SELECT 6,1,'duplicate pairs detected per month', left(detected_at,7), count(*)::text FROM tender_duplicates GROUP BY 4
UNION ALL SELECT 6,2,'tenders carrying a duplicate link (all | surfaced)', 'count',
       count(*)::text||' | '||count(*) FILTER (WHERE EXISTS (SELECT 1 FROM surf s WHERE s.tenant_id=dupt.tenant_id AND s.pub_number=dupt.pub))::text FROM dupt
UNION ALL SELECT 6,3,'status of surfaced tenders with a duplicate link', s.status, count(*)::text
  FROM surf s JOIN dupt ON dupt.tenant_id=s.tenant_id AND dupt.pub=s.pub_number GROUP BY s.status
UNION ALL SELECT 6,4,'pair outcome (both sides status)', LEAST(a.status,b.status)||' + '||GREATEST(a.status,b.status), count(*)::text
  FROM tender_duplicates d
  JOIN t a ON a.tenant_id=d.tenant_id AND a.pub_number=d.pub_number_a
  JOIN t b ON b.tenant_id=d.tenant_id AND b.pub_number=d.pub_number_b GROUP BY 4
UNION ALL SELECT 6,5,'dismissed with reason_category=duplicate', 'count', count(*)::text FROM t WHERE reason_category='duplicate'
UNION ALL SELECT 6,6,'dedup similarity threshold rows (non-default 0.75)', 'rows | non-default', count(*)::text||' | '||count(*) FILTER (WHERE similarity_threshold<>0.75)::text FROM tenant_dedup_settings

-- ================= 07 SCREENING =================
UNION ALL SELECT 7,0,'screening questions (total | enabled)', 'tenant '||tenant_id, count(*)::text||' | '||count(*) FILTER (WHERE enabled::text IN ('true','1'))::text FROM tenant_screening_questions GROUP BY tenant_id
UNION ALL SELECT 7,1,'screening answers by status', status, count(*)::text FROM tender_screening_answers GROUP BY status
UNION ALL SELECT 7,2,'answer rate per question (answered | not_stated)', left(q.question_text,80),
       count(*) FILTER (WHERE a.status='answered')::text||' | '||count(*) FILTER (WHERE a.status='not_stated')::text
  FROM tenant_screening_questions q LEFT JOIN tender_screening_answers a ON a.question_id=q.id GROUP BY q.id, q.question_text
UNION ALL SELECT 7,3,'surfaced tenders with >=1 answer', 'count', count(DISTINCT (a.tenant_id, a.pub_number))::text
  FROM tender_screening_answers a JOIN surf s ON s.tenant_id=a.tenant_id AND s.pub_number=a.pub_number

-- ================= 08 MANUAL TENDERS =================
UNION ALL SELECT 8,0,'manual tenders (incl deleted)', COALESCE(NULLIF(exclude_reason,''),'(kept)')||' / deleted='||(deleted_at IS NOT NULL)::text, count(*)::text
  FROM tenders WHERE entry_method='manual' GROUP BY 4
UNION ALL SELECT 8,1,'manual tenders flagged would-be-excluded (detail)', pub_number,
       exclude_reason||' | value_eur='||COALESCE(value_eur,'')||' | status='||status||' | deleted='||(deleted_at IS NOT NULL)::text
  FROM tenders WHERE entry_method='manual' AND exclude_reason<>''
UNION ALL SELECT 8,2,'manual drafts by status', status, count(*)::text FROM manual_tender_drafts GROUP BY status
UNION ALL SELECT 8,3,'origin sites (total | active)', 'count', count(*)::text||' | '||count(*) FILTER (WHERE active::text IN ('true','1'))::text FROM tenant_origin_sites
UNION ALL SELECT 8,4,'manual counters', tenant_id||'/'||year, last_value::text FROM manual_tender_counters

-- ================= 09 TRIM CANDIDATES: ROW COUNTS =================
UNION ALL SELECT 9,0,'rows','documents', count(*)::text FROM documents
UNION ALL SELECT 9,1,'rows','translations (DeepL text cache)', count(*)::text FROM translations
UNION ALL SELECT 9,2,'rows translated_documents by status', status, count(*)::text FROM translated_documents GROUP BY status
UNION ALL SELECT 9,3,'rows','document_tender_links (active | detached)', count(*) FILTER (WHERE detached_at IS NULL)::text||' | '||count(detached_at)::text FROM document_tender_links
UNION ALL SELECT 9,4,'rows','vault_documents', count(*)::text FROM vault_documents
UNION ALL SELECT 9,5,'rows','vault_document_history', count(*)::text FROM vault_document_history
UNION ALL SELECT 9,6,'rows','composer_documents', count(*)::text FROM composer_documents
UNION ALL SELECT 9,7,'rows','composer_document_history', count(*)::text FROM composer_document_history
UNION ALL SELECT 9,8,'rows','composer_matrix', count(*)::text FROM composer_matrix
UNION ALL SELECT 9,9,'rows','composer_requirements', count(*)::text FROM composer_requirements
UNION ALL SELECT 9,10,'rows','composer_requirement_history', count(*)::text FROM composer_requirement_history
UNION ALL SELECT 9,11,'rows','composer_style_examples', count(*)::text FROM composer_style_examples
UNION ALL SELECT 9,12,'rows','tenant_style_guide', count(*)::text FROM tenant_style_guide
UNION ALL SELECT 9,13,'rows','pipeline_history', count(*)::text FROM pipeline_history
UNION ALL SELECT 9,14,'rows email_alerts (source / parse_status)', source||' / '||parse_status, count(*)::text FROM email_alerts GROUP BY source, parse_status
UNION ALL SELECT 9,15,'rows','award fields: awarded_to set | award_detail set', count(awarded_to)::text||' | '||count(award_detail)::text FROM t
UNION ALL SELECT 9,16,'rows','notice_detail set | internal_identifier set', count(notice_detail)::text||' | '||count(internal_identifier)::text FROM t
UNION ALL SELECT 9,17,'pg_stat live rows (every table, incl. retired)', relname, n_live_tup::text FROM pg_stat_user_tables

-- ================= 10 LLM USAGE (CR-015) =================
UNION ALL SELECT 10,0,'llm_usage', month||' / '||feature||' / '||model, calls::text||' calls, in '||input_tokens::text||', out '||output_tokens::text FROM llm_usage
UNION ALL SELECT 10,1,'llm_usage_months', month, calls::text FROM llm_usage_months

-- ================= 11 SOURCE HEALTH =================
UNION ALL SELECT 11,0,'source health (runs | failed | last ok | last failed | avg pulled)', source,
       count(*)::text||' | '||count(*) FILTER (WHERE status='failed')::text
       ||' | '||COALESCE(max(run_date) FILTER (WHERE status='ok'),'-')||' | '||COALESCE(max(run_date) FILTER (WHERE status='failed'),'-')
       ||' | '||COALESCE(round(avg(notices_pulled))::text,'-')
  FROM source_health GROUP BY source
UNION ALL SELECT 11,1,'source health last 30 days (runs | failed)', source,
       count(*)::text||' | '||count(*) FILTER (WHERE status='failed')::text
  FROM source_health WHERE run_date >= to_char(current_date-30,'YYYY-MM-DD') GROUP BY source
UNION ALL SELECT * FROM (
  SELECT 11 AS s, row_number() OVER (ORDER BY count(*) DESC)+100 AS o, 'top failure messages' AS section, source||': '||left(COALESCE(error_detail,''),100) AS metric, count(*)::text AS value
  FROM source_health WHERE status='failed' GROUP BY source, left(COALESCE(error_detail,''),100)) y WHERE o <= 120
UNION ALL SELECT 11,2,'portals (tenant / name / type)', tenant_id||' / '||name||' / '||type, enabled::text FROM tenant_portals

-- ================= 12 CONFIG / SETTINGS AT DEFAULT =================
UNION ALL SELECT 12,0,'tenant_cpv codes', 'tenant '||tenant_id, count(*)::text FROM tenant_cpv GROUP BY tenant_id
UNION ALL SELECT 12,1,'tenant_keywords terms per lang', 'tenant '||k.tenant_id||' / '||e.key, jsonb_array_length(e.value)::text
  FROM tenant_keywords k, LATERAL jsonb_each(k.terms::jsonb) e WHERE jsonb_typeof(e.value)='array'
UNION ALL SELECT 12,2,'tenant_keywords distinctive', 'tenant '||tenant_id, jsonb_array_length(distinctive::jsonb)::text FROM tenant_keywords
UNION ALL SELECT 12,3,'tenant_settings (freq / window / notify)', 'tenant '||tenant_id,
       run_frequency||' / '||run_window_start||'-'||run_window_end||' / '||notify_on_complete::text||' '||(notify_email<>'')::text FROM tenant_settings
UNION ALL SELECT 12,4,'settings rows present (lazy-default: 0 = never changed)', 'tenant_settings', count(*)::text FROM tenant_settings
UNION ALL SELECT 12,5,'settings rows present (lazy-default: 0 = never changed)', 'tenant_vault_settings', count(*)::text FROM tenant_vault_settings
UNION ALL SELECT 12,6,'settings rows present (lazy-default: 0 = never changed)', 'tenant_vault_rules', count(*)::text FROM tenant_vault_rules
UNION ALL SELECT 12,7,'settings rows present (lazy-default: 0 = never changed)', 'tenant_composer_settings', count(*)::text FROM tenant_composer_settings
UNION ALL SELECT 12,8,'settings rows present (lazy-default: 0 = never changed)', 'tenant_dedup_settings', count(*)::text FROM tenant_dedup_settings
UNION ALL SELECT 12,9,'settings rows present (lazy-default: 0 = never changed)', 'tenant_scout_settings', count(*)::text FROM tenant_scout_settings
UNION ALL SELECT 12,10,'scout deadline_floor_hours', 'tenant '||tenant_id, deadline_floor_hours::text FROM tenant_scout_settings
UNION ALL SELECT 12,11,'alembic version', 'version_num', string_agg(version_num, ',') FROM alembic_version
)
SELECT s, o, section, metric, value FROM r ORDER BY s, o, section, metric;
