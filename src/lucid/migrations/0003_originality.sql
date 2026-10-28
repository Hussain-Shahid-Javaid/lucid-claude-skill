-- Invention / originality workflow (feature 23).
CREATE TABLE invention_studies (
  id TEXT PRIMARY KEY, user_scope TEXT NOT NULL DEFAULT 'default', project_scope TEXT NOT NULL DEFAULT '',
  target TEXT NOT NULL,              -- JSON: problem, users, constraints, resources, existing approaches, improvement, originality level
  ledger_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE invention_candidates (
  id TEXT PRIMARY KEY, study_id TEXT NOT NULL, title TEXT NOT NULL, mechanism TEXT NOT NULL,
  difference TEXT NOT NULL, method TEXT NOT NULL, advantage TEXT, components TEXT NOT NULL DEFAULT '[]',
  domain TEXT, revisit_of TEXT, revisit_reason TEXT,
  cosmetic_flags TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'not_assessed',  -- known|adaptation|potentially_original|unresolved|not_assessed
  scope_label TEXT,                             -- new_to_user|new_within_searched_collection|potentially_new_more_broadly
  assessment TEXT NOT NULL DEFAULT '{}', value TEXT NOT NULL DEFAULT '{}',
  rejected INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

-- Every search attempt is logged, including the ones that could not run.
CREATE TABLE prior_art_searches (
  id TEXT PRIMARY KEY, study_id TEXT NOT NULL, candidate_id TEXT, provider TEXT NOT NULL, query TEXT NOT NULL,
  query_kind TEXT NOT NULL,          -- problem|mechanism|synonym|adjacent|component|combination|internal
  status TEXT NOT NULL,              -- ran|unavailable|not_authorized|skipped_confidential
  result_count INTEGER NOT NULL DEFAULT 0, note TEXT,
  covers TEXT NOT NULL DEFAULT '[]',  -- source kinds this search covered (papers, products, repos, ...)
  searched_at TEXT NOT NULL
);

CREATE TABLE prior_art_sources (
  id TEXT PRIMARY KEY, study_id TEXT NOT NULL, candidate_id TEXT, provider TEXT NOT NULL, kind TEXT NOT NULL,
  ref TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL, inspected INTEGER NOT NULL DEFAULT 0,
  similarity REAL, found_by TEXT,
  relation TEXT,                     -- set by whoever inspected it: equivalent|adaptation|different|unclear
  differences TEXT, added_at TEXT NOT NULL
);

CREATE TABLE independent_assessments (
  id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, role TEXT NOT NULL, verdict TEXT NOT NULL,
  rationale TEXT NOT NULL, saw_others INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);

CREATE INDEX idx_invention_candidates_study ON invention_candidates(study_id);
CREATE INDEX idx_prior_art_sources_candidate ON prior_art_sources(candidate_id);
CREATE INDEX idx_prior_art_searches_candidate ON prior_art_searches(candidate_id);
