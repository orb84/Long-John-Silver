-- Round 109: user-created scheduled prompts, one-off reminders, and due-time checks.
-- Existing recurring prompt tasks remain valid. New columns are optional/defaulted.
ALTER TABLE scheduled_tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'scheduled_prompt';
ALTER TABLE scheduled_tasks ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'recurring';
ALTER TABLE scheduled_tasks ADD COLUMN title TEXT NOT NULL DEFAULT '';
ALTER TABLE scheduled_tasks ADD COLUMN due_at TEXT;
ALTER TABLE scheduled_tasks ADD COLUMN next_run_at TEXT;
ALTER TABLE scheduled_tasks ADD COLUMN run_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE scheduled_tasks ADD COLUMN max_runs INTEGER;
ALTER TABLE scheduled_tasks ADD COLUMN session_id TEXT;
ALTER TABLE scheduled_tasks ADD COLUMN last_error TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_due ON scheduled_tasks(enabled, next_run_at, due_at);
