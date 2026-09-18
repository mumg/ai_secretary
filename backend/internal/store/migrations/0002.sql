ALTER TABLE tasks ADD COLUMN due_reminder_sent_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE tasks ADD COLUMN overdue_notification_date DATE;

