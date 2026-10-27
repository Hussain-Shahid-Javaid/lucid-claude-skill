-- Conversations become unique per (user_scope, provider, account, external_id) so two users
-- can never write into each other's conversations. Runs with foreign_keys OFF (see db.migrate).
CREATE TABLE conversations_new (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, account TEXT NOT NULL DEFAULT 'default',
  external_id TEXT NOT NULL, title TEXT, created_at TEXT, updated_at TEXT,
  import_method TEXT NOT NULL, coverage TEXT NOT NULL DEFAULT 'full', current_leaf TEXT,
  user_scope TEXT NOT NULL DEFAULT 'default', project_scope TEXT NOT NULL DEFAULT '',
  origin_lucid INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
  metadata TEXT NOT NULL DEFAULT '{}',
  UNIQUE(user_scope, provider, account, external_id)
);
INSERT INTO conversations_new SELECT id, provider, account, external_id, title, created_at, updated_at, import_method,
  coverage, current_leaf, user_scope, project_scope, origin_lucid, status, metadata FROM conversations;
DROP TABLE conversations;
ALTER TABLE conversations_new RENAME TO conversations;
UPDATE tombstones SET natural_key = 'conv:' || COALESCE((SELECT user_scope FROM conversations c WHERE c.id = tombstones.entity_id), 'default') || '|' || substr(natural_key, 6) WHERE entity_type = 'conversation';
