-- Optional personal avatar and meeting representation (feature 28).
CREATE TABLE avatar_enrollments (
  id TEXT PRIMARY KEY, user_scope TEXT NOT NULL, display_name TEXT NOT NULL,
  provider TEXT NOT NULL,                     -- local-still (built in) or a vendor name
  status TEXT NOT NULL,                       -- draft|preview_ready|active|revoked|deleted
  appearance_enabled INTEGER NOT NULL DEFAULT 0, voice_enabled INTEGER NOT NULL DEFAULT 0,
  permitted_uses TEXT NOT NULL DEFAULT '[]', provider_verification TEXT NOT NULL DEFAULT 'not_started',
  preview TEXT, retention TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE avatar_consents (
  id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL, kind TEXT NOT NULL,   -- likeness|voice
  statement TEXT NOT NULL, signed_by TEXT NOT NULL, method TEXT NOT NULL, evidence TEXT,
  granted_at TEXT NOT NULL, revoked_at TEXT
);
CREATE TABLE avatar_media (
  id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL, kind TEXT NOT NULL,   -- photo|video|voice_sample|consent_video
  path TEXT NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, added_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE avatar_meeting_auth (
  id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL, rules TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE avatar_sessions (
  id TEXT PRIMARY KEY, enrollment_id TEXT NOT NULL, meeting_id TEXT NOT NULL, auth_id TEXT NOT NULL,
  transport TEXT NOT NULL, verification_level TEXT NOT NULL,   -- simulated|provider_sandbox|live
  state TEXT NOT NULL, identity TEXT NOT NULL, recording TEXT NOT NULL DEFAULT 'off', transcription TEXT NOT NULL DEFAULT 'off',
  started_at TEXT NOT NULL, ended_at TEXT, end_reason TEXT, summary TEXT
);
CREATE UNIQUE INDEX idx_avatar_one_live_session ON avatar_sessions(meeting_id) WHERE ended_at IS NULL;
CREATE TABLE avatar_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, t REAL NOT NULL, kind TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE avatar_costs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, category TEXT NOT NULL,   -- avatar|speech_in|speech_out|meeting
  units REAL NOT NULL, unit TEXT NOT NULL, usd REAL NOT NULL, at REAL NOT NULL
);
