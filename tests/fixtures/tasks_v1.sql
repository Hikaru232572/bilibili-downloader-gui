-- Original task schema, retained as an upgrade fixture.
PRAGMA user_version=1;
CREATE TABLE batches (
 id TEXT PRIMARY KEY, state TEXT NOT NULL, notification_claimed INTEGER NOT NULL DEFAULT 0,
 created REAL NOT NULL, ended REAL, summary TEXT
);
CREATE UNIQUE INDEX one_open_batch ON batches(state) WHERE state='open';
CREATE TABLE tasks (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 id TEXT NOT NULL UNIQUE, submission_key TEXT NOT NULL UNIQUE,
 spec TEXT NOT NULL, state TEXT NOT NULL, batch_id TEXT NOT NULL REFERENCES batches(id),
 attempt_id TEXT, error_code TEXT, message TEXT, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE UNIQUE INDEX one_active_task ON tasks((1))
 WHERE state IN ('running','validating','cancel_requested');
CREATE TABLE outputs (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), item_id TEXT NOT NULL,
 component TEXT NOT NULL, original_index INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', path TEXT, temporary_path TEXT,
 evidence TEXT, error_code TEXT, message TEXT,
 UNIQUE(task_id,item_id,component)
);
CREATE TABLE attempts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), started REAL NOT NULL,
 ended REAL, state TEXT NOT NULL, backend_revision TEXT NOT NULL DEFAULT ''
);
CREATE TABLE preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL);
