-- schema version 1

CREATE TABLE IF NOT EXISTS recruitments (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  guild_id        INTEGER NOT NULL,
  channel_id      INTEGER NOT NULL,
  message_id      INTEGER UNIQUE,
  creator_user_id INTEGER NOT NULL,
  title           TEXT    NOT NULL,
  mode            TEXT    NOT NULL CHECK (mode IN ('message', 'button', 'both')),
  capacity        INTEGER NOT NULL CHECK (capacity BETWEEN 2 AND 10),
  start_time      TEXT,
  status          TEXT    NOT NULL DEFAULT 'open'
                  CHECK (status IN ('open', 'closed', 'cancelled')),
  created_at      TEXT    NOT NULL,
  closed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_recruitments_channel_status
  ON recruitments (channel_id, status);

-- 1チャンネルにつきアクティブなメッセージ式（message / both）募集は1件だけ
CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_message_recruit_per_channel
  ON recruitments (channel_id)
  WHERE status = 'open' AND mode IN ('message', 'both');

CREATE TABLE IF NOT EXISTS participants (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  recruitment_id INTEGER NOT NULL REFERENCES recruitments (id) ON DELETE CASCADE,
  user_id        INTEGER NOT NULL,
  status         TEXT    NOT NULL DEFAULT 'joined' CHECK (status IN ('joined', 'waitlist')),
  source         TEXT    NOT NULL CHECK (source IN ('button', 'message', 'command', 'creator')),
  joined_at      TEXT    NOT NULL,
  UNIQUE (recruitment_id, user_id)
);

-- AI判定の監査ログ（チューニング用）。メッセージ本文は保存しない。
CREATE TABLE IF NOT EXISTS ai_judgements (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  recruitment_id INTEGER NOT NULL,
  message_id     INTEGER NOT NULL,
  user_id        INTEGER NOT NULL,
  action         TEXT    NOT NULL CHECK (action IN ('JOIN', 'LEAVE', 'NONE', 'ERROR')),
  confidence     REAL,
  applied        INTEGER NOT NULL,
  created_at     TEXT    NOT NULL
);
