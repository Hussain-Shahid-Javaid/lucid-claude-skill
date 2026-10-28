-- Email, Slack, Microsoft Teams and other team tools (feature 27).
CREATE TABLE comm_accounts (
  id TEXT PRIMARY KEY, user_scope TEXT NOT NULL, provider TEXT NOT NULL,   -- slack|gmail|outlook|teams|<connector id>
  identity TEXT NOT NULL,                     -- address, Slack/Teams user or bot id the provider sends as
  identity_kind TEXT NOT NULL,                -- user_delegated|bot|app|send_as_alias
  display TEXT NOT NULL DEFAULT '', workspace TEXT NOT NULL DEFAULT '',   -- Slack team id, Teams tenant, mail domain
  team_scope TEXT NOT NULL DEFAULT '',        -- project scope used for team memory from this workspace
  granted_scopes TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'active',   -- active|revoked|expired
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE comm_policies (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, mode TEXT NOT NULL,       -- draft_only|send_after_review|auto
  rules TEXT NOT NULL,                        -- recipients, channels, topics, actions, hours, limits, sharing, commitments, escalation
  expires_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE comm_messages (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, user_scope TEXT NOT NULL, op_id TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,                         -- email|chat
  status TEXT NOT NULL,                       -- draft|pending_review|queued|sending|sent|outcome_unknown|failed|blocked|escalated|canceled
  mode TEXT NOT NULL, recipients TEXT NOT NULL, channel TEXT, thread TEXT, subject TEXT, body TEXT NOT NULL,
  attachments TEXT NOT NULL DEFAULT '[]', content_hash TEXT NOT NULL, checks TEXT NOT NULL DEFAULT '{}',
  approved_hash TEXT, approved_by TEXT, in_reply_to TEXT, provider_ref TEXT, receipt TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, lookups INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE comm_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT, account_id TEXT, action TEXT NOT NULL,
  actor TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}', at TEXT NOT NULL
);
CREATE TABLE comm_inbound (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, external_id TEXT NOT NULL, channel TEXT, thread TEXT,
  sender TEXT NOT NULL, visibility TEXT NOT NULL,   -- dm|private|channel|email
  audience TEXT NOT NULL DEFAULT '[]', text TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '[]',
  received_at TEXT NOT NULL, UNIQUE(account_id, external_id)
);
CREATE INDEX idx_comm_messages_status ON comm_messages(status, next_attempt_at);
CREATE INDEX idx_comm_audit_message ON comm_audit(message_id);
