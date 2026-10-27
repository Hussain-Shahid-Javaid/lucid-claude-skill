-- Lucid schema v1. All timestamps are ISO-8601 UTC strings.

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- Captured sources: transcripts, pages, files, tool output, assistant output.
CREATE TABLE sources (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,            -- transcript|message|web|file|user|assistant|tool|import|experiment
  uri TEXT, title TEXT, provider TEXT, account TEXT, external_id TEXT,
  captured_at TEXT NOT NULL,
  content TEXT,                  -- optional captured text (redacted)
  content_hash TEXT,
  user_scope TEXT NOT NULL DEFAULT 'default',
  project_scope TEXT NOT NULL DEFAULT '',
  origin_lucid INTEGER NOT NULL DEFAULT 0,   -- 1 when the content was produced by Lucid itself
  lineage TEXT NOT NULL DEFAULT '[]',        -- JSON list of upstream ids (echo detection)
  metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_sources_hash ON sources(content_hash);

CREATE TABLE memories (
  id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL DEFAULT 1,
  user_scope TEXT NOT NULL DEFAULT 'default',
  project_scope TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,            -- preference|project_state|task_state|decision|action|outcome|correction|lesson|fact|claim|summary
  key TEXT,                      -- optional subject key used for conflict detection, e.g. "pref:test-runner"
  content TEXT NOT NULL,
  rationale TEXT,
  origin TEXT NOT NULL,          -- user|assistant|tool|import|lucid_summary|experiment
  verification TEXT NOT NULL DEFAULT 'unverified',  -- verified|unverified|disputed|refuted
  confidence REAL,
  uncertainty TEXT,
  created_at TEXT NOT NULL, observed_at TEXT, updated_at TEXT NOT NULL, expires_at TEXT,
  revision INTEGER NOT NULL DEFAULT 1,
  supersedes TEXT, superseded_by TEXT,
  status TEXT NOT NULL DEFAULT 'active',   -- active|superseded|deleted
  sharing TEXT NOT NULL DEFAULT 'private', -- private|project|shared
  retention TEXT,                          -- e.g. "90d" or NULL for indefinite
  tags TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL
);
CREATE INDEX idx_mem_scope ON memories(user_scope, project_scope, status);
CREATE INDEX idx_mem_key ON memories(key);

CREATE VIRTUAL TABLE memories_fts USING fts5(memory_id UNINDEXED, content, tags, kind UNINDEXED, tokenize='porter unicode61');

CREATE TABLE memory_evidence (
  memory_id TEXT NOT NULL, source_id TEXT, message_id TEXT, quote TEXT,
  relation TEXT NOT NULL DEFAULT 'supports',   -- supports|contradicts|derived_from
  PRIMARY KEY (memory_id, source_id, message_id, relation)
);

CREATE TABLE memory_conflicts (
  id TEXT PRIMARY KEY, memory_a TEXT NOT NULL, memory_b TEXT NOT NULL, reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', resolution TEXT, created_at TEXT NOT NULL, resolved_at TEXT
);

-- Deletion tombstones prevent resurrection by reimport or stale clients.
CREATE TABLE tombstones (
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, natural_key TEXT,
  deleted_at TEXT NOT NULL, reason TEXT,
  PRIMARY KEY (entity_type, entity_id)
);
CREATE INDEX idx_tomb_key ON tombstones(natural_key);

CREATE TABLE conversations (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, account TEXT NOT NULL DEFAULT 'default',
  external_id TEXT NOT NULL, title TEXT, created_at TEXT, updated_at TEXT,
  import_method TEXT NOT NULL, coverage TEXT NOT NULL DEFAULT 'full', current_leaf TEXT,
  user_scope TEXT NOT NULL DEFAULT 'default', project_scope TEXT NOT NULL DEFAULT '',
  origin_lucid INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE(provider, account, external_id)
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
  external_id TEXT NOT NULL, parent_external_id TEXT,
  role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT,
  revision_of TEXT, attachments TEXT NOT NULL DEFAULT '[]',
  origin TEXT NOT NULL DEFAULT '{}', content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE(conversation_id, external_id)
);
CREATE INDEX idx_msg_parent ON messages(conversation_id, parent_external_id);

CREATE TABLE imports (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, method TEXT NOT NULL, file_hash TEXT,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  stats TEXT NOT NULL DEFAULT '{}', errors TEXT NOT NULL DEFAULT '[]', checkpoint TEXT
);

-- Idempotency: every externally-triggered mutation carries an op_id.
CREATE TABLE operations (
  op_id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT
);

CREATE TABLE outbox (
  id TEXT PRIMARY KEY, op_id TEXT NOT NULL, destination TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|in_flight|done|failed|outcome_unknown|conflict|dead
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_error TEXT, receipt TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(op_id, destination)
);

CREATE TABLE checkpoints (stream TEXT PRIMARY KEY, position TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE sharing_rules (
  id TEXT PRIMARY KEY, destination TEXT NOT NULL, categories TEXT NOT NULL,
  user_scope TEXT NOT NULL DEFAULT 'default', project_scope TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);

CREATE TABLE handoffs (
  id TEXT PRIMARY KEY, destination TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
  memory_ids TEXT NOT NULL DEFAULT '[]', consumed_at TEXT, consumed_by TEXT
);

CREATE TABLE budget_ledgers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, token_limit INTEGER, cost_limit REAL, time_limit_s REAL,
  reserve_fraction REAL NOT NULL DEFAULT 0.15, mode TEXT NOT NULL DEFAULT 'estimated',
  created_at TEXT NOT NULL, started_at TEXT
);
CREATE TABLE budget_entries (
  id TEXT PRIMARY KEY, ledger_id TEXT NOT NULL, purpose TEXT NOT NULL, actor TEXT,
  status TEXT NOT NULL,              -- reserved|reconciled|released|unknown
  reserved_tokens INTEGER NOT NULL, reserved_cost REAL NOT NULL DEFAULT 0,
  input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER, reasoning_tokens INTEGER,
  actual_cost REAL, use_reserve INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, parent_id TEXT, title TEXT NOT NULL, objective TEXT,
  acceptance TEXT NOT NULL DEFAULT '[]', essential INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|running|completed|partially_completed|blocked|budget_exhausted|canceled|paused|failed
  depends_on TEXT NOT NULL DEFAULT '[]', owned_paths TEXT NOT NULL DEFAULT '[]',
  ledger_id TEXT, worker TEXT, attempts INTEGER NOT NULL DEFAULT 0, depth INTEGER NOT NULL DEFAULT 0,
  checkpoint TEXT NOT NULL DEFAULT '{}', result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE authorizations (
  id TEXT PRIMARY KEY, category TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '*',
  limits TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, expires_at TEXT, revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE questions (
  id TEXT PRIMARY KEY, task_id TEXT, category TEXT NOT NULL, decision TEXT NOT NULL,
  options TEXT NOT NULL, default_option TEXT, deadline TEXT NOT NULL, scope TEXT, consequence TEXT,
  authorization_id TEXT, required_approval INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',   -- open|answered|defaulted|blocked|canceled|paused
  answer TEXT, resolved_by TEXT, resolved_at TEXT, created_at TEXT NOT NULL, late_responses TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE verifications (
  id TEXT PRIMARY KEY, task_id TEXT, subject TEXT NOT NULL, iteration INTEGER NOT NULL,
  original TEXT, revised TEXT, checks TEXT NOT NULL DEFAULT '[]', changed INTEGER NOT NULL DEFAULT 0,
  initially_correct INTEGER, finally_correct INTEGER, remaining_uncertainty TEXT, stop_reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE predictions (
  id TEXT PRIMARY KEY, task_id TEXT, claim TEXT NOT NULL, confidence REAL NOT NULL,
  outcome INTEGER, created_at TEXT NOT NULL, resolved_at TEXT
);

CREATE TABLE monitor_items (
  id TEXT PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open', evidence TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE experiments (
  id TEXT PRIMARY KEY, module TEXT NOT NULL, title TEXT NOT NULL, hypothesis TEXT, prediction TEXT,
  config TEXT NOT NULL DEFAULT '{}', result TEXT, status TEXT NOT NULL DEFAULT 'planned',
  criticism TEXT, revision_of TEXT, cost TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE connectors (
  id TEXT PRIMARY KEY, manifest TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'unconfigured', last_error TEXT, updated_at TEXT NOT NULL
);

CREATE TABLE capture_sites (
  site TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, auto_capture INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);

CREATE TABLE activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  entity_type TEXT, entity_id TEXT, detail TEXT NOT NULL DEFAULT '{}'
);
