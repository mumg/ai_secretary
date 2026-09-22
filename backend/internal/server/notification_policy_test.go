package server

import (
	"context"
	"errors"
	"testing"
	"time"
)

type recordedPush struct {
	endpoint string
	notice   pushNotice
}
type fakePush struct {
	targets []string
	failed  map[string]bool
	sent    []recordedPush
}

func (f *fakePush) Targets(context.Context) ([]string, error) { return f.targets, nil }
func (f *fakePush) Send(_ context.Context, endpoint string, n pushNotice) error {
	f.sent = append(f.sent, recordedPush{endpoint, n})
	if f.failed[endpoint] {
		return errors.New("offline")
	}
	return nil
}
func policyJob(t *testing.T, s *Server, fn func(*request)) {
	t.Helper()
	if _, err := s.job(context.Background(), func(q *request) bool { fn(q); return true }); err != nil {
		t.Fatal(err)
	}
}
func policySQL(t *testing.T, s *Server, sql string, args ...any) {
	t.Helper()
	if _, err := s.Pool.Exec(context.Background(), sql, args...); err != nil {
		t.Fatal(err)
	}
}
func policyCount(t *testing.T, s *Server, table string) int {
	t.Helper()
	var n int
	if err := s.Pool.QueryRow(context.Background(), "SELECT count(*) FROM "+table).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}
func policyTask(t *testing.T, s *Server) string {
	t.Helper()
	var id string
	policyJob(t, s, func(q *request) {
		id = str(q.insert("tasks", M{"title": "Test notification", "manually_created": false}), "id")
	})
	return id
}
func policyAge(t *testing.T, s *Server) {
	policySQL(t, s, "UPDATE notification_queue SET created_at=now()-interval '20 minutes'")
}
func policySettings(t *testing.T, s *Server, n M) {
	t.Helper()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"notifications": n}}, 200)
}

func TestNotificationQueueCoalescesAndSurvivesRestart(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one", "two"}}
	s.push = f
	id := policyTask(t, s)
	policyJob(t, s, func(q *request) { q.notify("NEW_TASK", id) })
	policyTask(t, s)
	if policyCount(t, s, "notification_queue") != 2 {
		t.Fatal("same task did not coalesce")
	}
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 0 {
		t.Fatal("sent before aggregation window")
	}
	policyAge(t, s)
	restarted := New(s.Pool, s.Config, s.Version)
	restarted.push = f
	restarted.dispatchNotifications(context.Background())
	if len(f.sent) != 2 || f.sent[0].notice.Kind != "SYNC" {
		t.Fatal(f.sent)
	}
	if policyCount(t, s, "notification_queue") != 0 {
		t.Fatal("queue not drained")
	}
	policyTask(t, s)
	policyAge(t, s)
	restarted.dispatchNotifications(context.Background())
	if len(f.sent) != 2 {
		t.Fatal("rate limit bypassed")
	}
	policySQL(t, s, "UPDATE notification_policy_state SET next_send_at=now()-interval '1 minute'")
	restarted.dispatchNotifications(context.Background())
	if len(f.sent) != 4 {
		t.Fatal(f.sent)
	}
}
func TestNotificationQueueRollsBackWithTask(t *testing.T) {
	s := testServer(t)
	_, err := s.job(context.Background(), func(q *request) bool {
		q.insert("tasks", M{"title": "Rolled back"})
		q.enqueueNotifications()
		fail(422, "abort")
		return true
	})
	if err == nil || policyCount(t, s, "notification_queue") != 0 || policyCount(t, s, "tasks") != 0 {
		t.Fatal("outbox escaped rollback")
	}
}
func TestNotificationRetryIsPerDeviceAndIdempotent(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one", "two"}, failed: map[string]bool{"two": true}}
	s.push = f
	policyTask(t, s)
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 2 {
		t.Fatal(f.sent)
	}
	var original string
	for _, send := range f.sent {
		if send.endpoint == "two" {
			original = send.notice.ID
		}
	}
	policySQL(t, s, "UPDATE notification_batches SET next_attempt_at=now()-interval '1 minute'")
	f.failed["two"] = false
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 3 || f.sent[2].endpoint != "two" || f.sent[2].notice.ID != original {
		t.Fatal("successful device retried or ID changed", f.sent)
	}
}
func TestNotificationRevalidatesPreparedBatch(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one"}, failed: map[string]bool{"one": true}}
	s.push = f
	id := policyTask(t, s)
	id2 := policyTask(t, s)
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	policySQL(t, s, "UPDATE tasks SET status='COMPLETED' WHERE id IN ($1,$2)", id, id2)
	policySQL(t, s, "UPDATE notification_batches SET next_attempt_at=now()-interval '1 minute'")
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 1 {
		t.Fatal("stale summary delivered")
	}
}
func TestNotificationHistoryIsSilent(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one"}}
	s.push = f
	policyJob(t, s, func(q *request) {
		q.insert("communication_sources", M{"id": "history", "label": "History", "source_type": "imap", "enabled": true})
		event := q.insert("communication_events", M{"source_id": "history", "source_type": "imap", "external_id": "1", "event_type": "email", "body": "Old mail", "occurred_at": time.Now().Add(-48 * time.Hour), "content_hash": "old"})
		// Reminders from imported history must also remain silent.
		task := q.insert("tasks", M{"title": "Imported overdue task", "source_event_id": event["id"], "due_at": time.Now().Add(-time.Hour)})
		q.notify("TASK_OVERDUE", str(task, "id"))
		q.enqueueNotifications()
		q.historicalNotifications = true
		for i := 0; i < 5; i++ {
			q.insert("tasks", M{"title": "Imported task"})
		}
	})
	if policyCount(t, s, "notification_queue") != 0 {
		t.Fatal("history queued notifications")
	}
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 0 {
		t.Fatal("history generated push before completion")
	}
	policySQL(t, s, "UPDATE communication_events SET analysis_state='COMPLETED'")
	s.dispatchNotifications(context.Background())
	policyJob(t, s, func(q *request) {
		for _, task := range q.rows("SELECT id FROM tasks WHERE source_event_id IS NOT NULL") {
			q.notify("TASK_OVERDUE", str(task, "id"))
		}
	})
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 0 || policyCount(t, s, "notification_queue") != 0 {
		t.Fatal("history generated a completion summary or later reminder", f.sent)
	}
}
func TestNotificationModesAndQuietHours(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one"}}
	s.push = f
	policySettings(t, s, M{"mode": "digest"})
	policyTask(t, s)
	policySQL(t, s, "UPDATE notification_queue SET created_at=now()-interval '3 minutes'")
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 0 {
		t.Fatal("digest too early")
	}
	now := time.Now().UTC()
	policySettings(t, s, M{"quiet_hours_enabled": true, "quiet_start": now.Add(-time.Hour).Format("15:04"), "quiet_end": now.Add(time.Hour).Format("15:04")})
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"server": M{"timezone": "UTC"}}}, 200)
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 0 {
		t.Fatal("quiet hours ignored")
	}
	policySettings(t, s, M{"quiet_hours_enabled": false})
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 1 {
		t.Fatal("quiet backlog not sent")
	}
	policySQL(t, s, "UPDATE notification_policy_state SET next_send_at=now()-interval '1 minute'")
	policySettings(t, s, M{"mode": "important"})
	policyJob(t, s, func(q *request) {
		id := str(q.insert("tasks", M{"title": "Confirmation", "manually_created": true, "status": "POSSIBLY_COMPLETED"}), "id")
		q.notify("TASK_POSSIBLY_COMPLETED", id)
	})
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 1 {
		t.Fatal("nonimportant event sent")
	}
}
func TestNotificationQuietAcrossMidnight(t *testing.T) {
	n := M{"quiet_hours_enabled": true, "quiet_start": "22:00", "quiet_end": "08:00"}
	for _, test := range []struct {
		hour  int
		quiet bool
	}{{21, false}, {22, true}, {0, true}, {7, true}, {8, false}} {
		now := time.Date(2026, 9, 22, test.hour, 0, 0, 0, time.FixedZone("user", 3*3600))
		if notificationQuiet(now, n) != test.quiet {
			t.Fatal(test)
		}
	}
}

func TestNotificationExpiredRetryBecomesSummary(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one", "two"}, failed: map[string]bool{"two": true}}
	s.push = f
	policyTask(t, s)
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	var oldID string
	for _, send := range f.sent {
		if send.endpoint == "two" {
			oldID = send.notice.ID
		}
	}
	policySQL(t, s, "UPDATE notification_batches SET expires_at=now()-interval '1 minute'")
	policySQL(t, s, "UPDATE notification_policy_state SET next_send_at=now()-interval '1 minute'")
	f.failed["two"] = false
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 3 || f.sent[2].endpoint != "two" || f.sent[2].notice.Kind != "SYNC" || f.sent[2].notice.ID == oldID {
		t.Fatal("expired batch replayed", f.sent)
	}
}
func TestNotificationWaitsWithoutDevices(t *testing.T) {
	s := testServer(t)
	f := &fakePush{}
	s.push = f
	policyTask(t, s)
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if policyCount(t, s, "notification_queue") != 1 || policyCount(t, s, "notification_batches") != 0 {
		t.Fatal("notification lost without devices")
	}
	f.targets = []string{"one"}
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 1 {
		t.Fatal("pending summary lost")
	}
}
func TestNotificationExpiryStopsAtQuietHours(t *testing.T) {
	now := time.Date(2026, 9, 22, 21, 58, 0, 0, time.FixedZone("user", 3*3600))
	n := M{"quiet_hours_enabled": true, "quiet_start": "22:00", "quiet_end": "08:00"}
	if expiry := notificationExpiry(now, n); !expiry.Equal(now.Add(2 * time.Minute)) {
		t.Fatal(expiry)
	}
}

func TestNotificationSourceAdditionBoundary(t *testing.T) {
	s := testServer(t)
	f := &fakePush{targets: []string{"one"}}
	s.push = f
	cutoff := time.Now().Add(-72 * time.Hour).UTC().Truncate(time.Second)
	policyJob(t, s, func(q *request) {
		q.insert("communication_sources", M{"id": "boundary", "label": "Boundary", "source_type": "imap", "enabled": true})
		q.exec("UPDATE communication_sources SET created_at=$1 WHERE id='boundary'", cutoff)
	})
	for _, test := range []struct {
		name     string
		occurred time.Time
		history  bool
	}{{"before", cutoff.Add(-time.Second), true}, {"equal", cutoff, true}, {"after", cutoff.Add(time.Second), false}} {
		t.Run(test.name, func(t *testing.T) {
			var eventID string
			policyJob(t, s, func(q *request) {
				event := q.insert("communication_events", M{"source_id": "boundary", "source_type": "imap", "external_id": test.name, "event_type": "email", "body": "Mail", "occurred_at": test.occurred, "content_hash": test.name})
				eventID = str(event, "id")
				q.insert("tasks", M{"title": test.name, "source_event_id": event["id"]})
			})
			var historical bool
			if err := s.Pool.QueryRow(context.Background(), "SELECT notification_history FROM communication_events WHERE id=$1", eventID).Scan(&historical); err != nil || historical != test.history {
				t.Fatal(historical, err)
			}
		})
	}
	// First sync has not completed, the new event is already more than a day old,
	// and history is still pending. None of these suppresses that new event.
	if policyCount(t, s, "notification_queue") != 1 {
		t.Fatal("wrong source cutoff")
	}
	policyAge(t, s)
	s.dispatchNotifications(context.Background())
	if len(f.sent) != 1 {
		t.Fatal("new event suppressed during initial import", f.sent)
	}
	// An old message discovered on a later poll still does not create a push.
	policyJob(t, s, func(q *request) {
		q.exec("UPDATE communication_sources SET last_sync_at=now() WHERE id='boundary'")
		event := q.insert("communication_events", M{"source_id": "boundary", "source_type": "imap", "external_id": "late-old", "event_type": "email", "body": "Late history", "occurred_at": cutoff.Add(-time.Hour), "content_hash": "late-old"})
		q.insert("tasks", M{"title": "Late history", "source_event_id": event["id"]})
	})
	if policyCount(t, s, "notification_queue") != 0 {
		t.Fatal("late history queued a notification")
	}
}
