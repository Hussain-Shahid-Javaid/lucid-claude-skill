-- Quality plans, revision-bound check evidence, defects, reviews and release gates (feature 26).
CREATE TABLE quality_plans (
  id TEXT PRIMARY KEY, subject TEXT NOT NULL, artifact TEXT NOT NULL,   -- path to the file, folder or repo checked
  profile TEXT NOT NULL,                                                -- trivial|standard|release|production
  criteria TEXT NOT NULL DEFAULT '[]',                                  -- acceptance criteria (category, text, mandatory)
  limitations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE quality_checks (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, description TEXT NOT NULL,
  mandatory INTEGER NOT NULL DEFAULT 1, command TEXT,                   -- JSON argv, or NULL for a manual check
  inputs TEXT NOT NULL DEFAULT '[]',                                    -- paths whose content this check depends on
  guards TEXT NOT NULL DEFAULT '[]',                                    -- paths (e.g. tests/) watched for weakening
  metric TEXT,                                                          -- JSON {regex, op, threshold, unit} for measurements
  definition_hash TEXT NOT NULL, est_seconds REAL, created_at TEXT NOT NULL, UNIQUE(plan_id, key)
);
CREATE TABLE quality_runs (
  id TEXT PRIMARY KEY, check_id TEXT NOT NULL, revision TEXT NOT NULL,  -- content hash of the check's inputs
  artifact_revision TEXT NOT NULL, definition_hash TEXT NOT NULL, guard_hash TEXT NOT NULL,
  outcome TEXT NOT NULL,                                                -- passed|failed|error
  evidence TEXT NOT NULL, measured REAL, duration_s REAL, actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE quality_defects (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, severity TEXT NOT NULL,   -- blocker|major|minor|trivial
  title TEXT NOT NULL, evidence TEXT NOT NULL, source TEXT NOT NULL,    -- check|review|user|escaped
  check_id TEXT, found_revision TEXT, status TEXT NOT NULL DEFAULT 'open', resolution TEXT,
  fixed_revision TEXT, escaped INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE quality_reviews (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, reviewer TEXT NOT NULL, independent INTEGER NOT NULL,
  examined TEXT NOT NULL, findings TEXT NOT NULL, artifact_revision TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE quality_alternatives (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, options TEXT NOT NULL, chosen TEXT, rationale TEXT,
  warnings TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TABLE quality_gates (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, artifact_revision TEXT NOT NULL, status TEXT NOT NULL,
  report TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX idx_quality_runs_check ON quality_runs(check_id, created_at);
CREATE INDEX idx_quality_defects_plan ON quality_defects(plan_id, status);
