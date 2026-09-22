package server

import (
	"context"
	"log/slog"
	"strings"
	"time"
)

// Settings belong to the single owner of this installation. Delivery receipts
// belong to endpoints, so a failing device never retries a successful device.
func (q *request) enqueueNotifications() {
	for _, n := range q.notifications {
		q.queueNotification(n[0], n[1])
	}
	q.notifications = nil
}
func importantNotification(kind string) bool {
	switch kind {
	case "CRITICAL_TASK", "NEW_TASK", "TASK_CONFIRMATION_REQUIRED", "TASK_REMINDER", "TASK_DUE_SOON", "TASK_OVERDUE":
		return true
	}
	return false
}
func (q *request) archivePending() bool {
	return len(q.rows("SELECT id FROM communication_events WHERE analysis_state IN ('PENDING','PROCESSING') AND notification_history LIMIT 1")) > 0
}
func (q *request) queueNotification(kind, id string) {
	// Source history is silent permanently, including reminders derived from it.
	if q.historicalNotifications || q.historicalTaskNotification(kind, id) {
		return
	}
	if kind == "DAILY_PLAN_READY" && q.archivePending() {
		return
	}
	key := kind + ":" + id
	if strings.Contains(kind, "TASK") {
		key = "task:" + id
	}
	q.exec(`INSERT INTO notification_queue(object_key,kind,object_id,important,historical) VALUES($1,$2,$3,$4,$5)
 ON CONFLICT(object_key) DO UPDATE SET kind=EXCLUDED.kind,important=notification_queue.important OR EXCLUDED.important,updated_at=now()`, key, kind, id, importantNotification(kind), false)
}
func notificationQuiet(now time.Time, n M) bool {
	if !boolean(n, "quiet_hours_enabled") {
		return false
	}
	clock := now.Format("15:04")
	start, end := str(n, "quiet_start"), str(n, "quiet_end")
	if start < end {
		return clock >= start && clock < end
	}
	return clock >= start || clock < end
}
func notificationWindow(n M) time.Duration {
	if str(n, "mode") == "digest" {
		return 15 * time.Minute
	}
	return 2 * time.Minute
}
func notificationInterval(n M) time.Duration {
	if str(n, "mode") == "digest" {
		return 15 * time.Minute
	}
	return 5 * time.Minute
}

// Never leave an alert queued at the provider across the start of quiet hours.
func notificationExpiry(now time.Time, n M) time.Time {
	expires := now.Add(5 * time.Minute)
	if boolean(n, "quiet_hours_enabled") {
		start, _ := time.Parse("15:04", str(n, "quiet_start"))
		boundary := time.Date(now.Year(), now.Month(), now.Day(), start.Hour(), start.Minute(), 0, 0, now.Location())
		if !boundary.After(now) {
			boundary = boundary.AddDate(0, 0, 1)
		}
		if boundary.Before(expires) {
			expires = boundary
		}
	}
	return expires
}

func (q *request) historicalTaskNotification(kind, id string) bool {
	if !strings.Contains(kind, "TASK") {
		return false
	}
	return len(q.rows("SELECT t.id FROM tasks t JOIN communication_events e ON e.id=t.source_event_id WHERE t.id=$1 AND (e.notification_history OR EXISTS (SELECT 1 FROM communication_sources s WHERE s.id=e.source_id AND e.occurred_at<=s.created_at))", id)) > 0
}
func (q *request) notificationRelevant(n M) bool {
	kind, id := str(n, "kind"), n["object_id"]
	if boolean(n, "historical") || q.historicalTaskNotification(kind, str(n, "object_id")) {
		return false
	}
	if strings.Contains(kind, "TASK") {
		tasks := q.rows("SELECT * FROM tasks WHERE id=$1 AND status NOT IN ('COMPLETED','CANCELLED')", id)
		if len(tasks) == 0 {
			return false
		}
		task := tasks[0]
		switch kind {
		case "TASK_REMINDER":
			return len(q.rows("SELECT id FROM reminders WHERE task_id=$1 AND enabled AND remind_at<=now()", id)) > 0
		case "TASK_POSSIBLY_COMPLETED":
			return task["status"] == "POSSIBLY_COMPLETED"
		case "TASK_CONFIRMATION_REQUIRED":
			return task["status"] == "NEEDS_CONFIRMATION"
		case "TASK_DUE_SOON":
			due := timestamp(task["due_at"])
			return due != nil && due.After(time.Now()) && due.Before(time.Now().Add(time.Duration(num(obj(q.settings(), "notifications"), "due_soon_minutes"))*time.Minute))
		case "TASK_OVERDUE":
			due := timestamp(task["due_at"])
			return due != nil && due.Before(time.Now())
		}
	}
	if kind == "MEETING_CONTEXT_READY" {
		return len(q.rows("SELECT id FROM meetings WHERE id=$1 AND status<>'CANCELLED' AND ends_at>now()", id)) > 0
	}
	if kind == "CHAT_RESPONSE_READY" {
		return len(q.rows("SELECT id FROM chat_requests WHERE id=$1 AND status='COMPLETED'", id)) > 0
	}
	if kind == "DAILY_PLAN_READY" {
		return boolean(obj(q.settings(), "notifications"), "daily_summary") && len(q.rows("SELECT id FROM daily_plans WHERE id=$1 AND plan_date=$2", id, q.now().Format("2006-01-02"))) > 0
	}
	return true
}

func (s *Server) dispatchNotifications(ctx context.Context) bool {
	transport := s.push
	if transport == nil {
		transport = &serverPushTransport{server: s}
	}
	// Persist the batch and target IDs before contacting a push provider.
	_, err := s.job(ctx, func(q *request) bool {
		locked := q.rows("SELECT * FROM notification_policy_state WHERE id=1 FOR UPDATE SKIP LOCKED")
		if len(locked) == 0 {
			return false
		}
		q.exec("DELETE FROM notification_batches WHERE completed_at<now()-interval '7 days'")
		settings := q.settings()
		n := obj(settings, "notifications")
		now := q.now()
		if notificationQuiet(now, n) {
			return false
		}
		active := q.rows("SELECT * FROM notification_batches WHERE completed_at IS NULL ORDER BY created_at LIMIT 1")
		if len(active) > 0 && timestamp(active[0]["expires_at"]).After(now) {
			return false
		}
		var next time.Time
		check(q.db.QueryRow(q.Context, "SELECT CASE WHEN next_send_at='-infinity' THEN '1970-01-01'::timestamptz ELSE next_send_at END FROM notification_policy_state WHERE id=1").Scan(&next))
		if now.Before(next) {
			return false
		}
		rows := q.rows("SELECT * FROM notification_queue ORDER BY created_at FOR UPDATE")
		eligible := []M{}
		ready := false
		for _, r := range rows {
			if !q.notificationRelevant(r) || (str(n, "mode") == "important" && !boolean(r, "important") && !boolean(r, "historical") && r["kind"] != "DAILY_PLAN_READY") {
				q.exec("DELETE FROM notification_queue WHERE object_key=$1", r["object_key"])
				continue
			}
			eligible = append(eligible, r)
			if now.Sub(*timestamp(r["created_at"])) >= notificationWindow(n) {
				ready = true
			}
		}
		if !ready {
			eligible = nil
		}
		if len(eligible) == 0 && len(active) == 0 {
			return false
		}
		targets, e := transport.Targets(ctx)
		if e != nil {
			return false
		}
		if len(targets) == 0 {
			return false
		}
		kind, id := "SYNC", newID()
		batchID := newID()
		if len(active) > 0 {
			batchID = str(active[0], "id")
			// The expired delivery becomes a current summary, never a replay of old alerts.
			q.exec("UPDATE notification_batches SET kind='SYNC',object_id=id,expires_at=$2,next_attempt_at=now() WHERE id=$1", batchID, notificationExpiry(now, n))
			q.exec("UPDATE notification_deliveries SET notification_id=gen_random_uuid(),attempts=0 WHERE batch_id=$1 AND accepted_at IS NULL", batchID)
			if len(eligible) > 0 {
				q.exec("UPDATE notification_deliveries SET notification_id=gen_random_uuid(),accepted_at=NULL,attempts=0 WHERE batch_id=$1 AND accepted_at IS NOT NULL", batchID)
			}
		} else {
			if len(eligible) == 1 && !boolean(eligible[0], "historical") && now.Sub(*timestamp(eligible[0]["created_at"])) < 15*time.Minute {
				kind = str(eligible[0], "kind")
				id = str(eligible[0], "object_id")
			}
			q.exec("INSERT INTO notification_batches(id,kind,object_id,expires_at) VALUES($1,$2,$3,$4)", batchID, kind, id, notificationExpiry(now, n))
		}
		for _, endpoint := range targets {
			q.exec("INSERT INTO notification_deliveries(batch_id,endpoint,notification_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING", batchID, endpoint, newID())
		}
		for _, r := range eligible {
			q.exec("INSERT INTO notification_batch_items(batch_id,object_key,kind,object_id,important,historical) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(batch_id,object_key) DO UPDATE SET kind=EXCLUDED.kind,object_id=EXCLUDED.object_id,important=EXCLUDED.important,historical=EXCLUDED.historical", batchID, r["object_key"], r["kind"], r["object_id"], r["important"], r["historical"])
			q.exec("DELETE FROM notification_queue WHERE object_key=$1", r["object_key"])
		}
		q.exec("UPDATE notification_policy_state SET next_send_at=$1 WHERE id=1", now.Add(notificationInterval(n)))
		return false
	})
	if err != nil {
		slog.Warn("notification batch preparation failed")
		return false
	}
	_, err = s.job(ctx, func(q *request) bool {
		if len(q.rows("SELECT id FROM notification_policy_state WHERE id=1 FOR UPDATE SKIP LOCKED")) == 0 {
			return false
		}
		if notificationQuiet(q.now(), obj(q.settings(), "notifications")) {
			return false
		}
		batches := q.rows("SELECT * FROM notification_batches WHERE completed_at IS NULL AND next_attempt_at<=now() AND expires_at>now() ORDER BY created_at LIMIT 1")
		if len(batches) == 0 {
			return false
		}
		batch := batches[0]
		settings := obj(q.settings(), "notifications")
		relevant := 0
		for _, item := range q.rows("SELECT * FROM notification_batch_items WHERE batch_id=$1", batch["id"]) {
			if q.notificationRelevant(item) && (str(settings, "mode") != "important" || boolean(item, "important") || boolean(item, "historical") || item["kind"] == "DAILY_PLAN_READY") {
				relevant++
			} else {
				q.exec("DELETE FROM notification_batch_items WHERE batch_id=$1 AND object_key=$2", batch["id"], item["object_key"])
			}
		}
		if relevant == 0 || !q.notificationRelevant(batch) {
			q.exec("UPDATE notification_batches SET completed_at=now() WHERE id=$1", batch["id"])
			return false
		}
		targets, e := transport.Targets(ctx)
		if e != nil {
			return false
		}
		available := map[string]bool{}
		for _, target := range targets {
			available[target] = true
		}
		for _, delivery := range q.rows("SELECT * FROM notification_deliveries WHERE batch_id=$1 AND accepted_at IS NULL", batch["id"]) {
			if !available[str(delivery, "endpoint")] {
				q.exec("DELETE FROM notification_deliveries WHERE batch_id=$1 AND endpoint=$2", batch["id"], delivery["endpoint"])
				continue
			}
			notice := pushNotice{ID: str(delivery, "notification_id"), Kind: str(batch, "kind"), ObjectID: str(batch, "object_id"), Expires: *timestamp(batch["expires_at"])}
			sendCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
			sendErr := transport.Send(sendCtx, str(delivery, "endpoint"), notice)
			cancel()
			if sendErr == nil {
				q.exec("UPDATE notification_policy_state SET next_send_at=GREATEST(next_send_at,$1) WHERE id=1", time.Now().Add(notificationInterval(settings)))
			}
			q.exec("UPDATE notification_deliveries SET attempts=attempts+1,accepted_at=CASE WHEN $3 THEN now() ELSE NULL END WHERE batch_id=$1 AND endpoint=$2", batch["id"], delivery["endpoint"], sendErr == nil)
		}
		q.exec("UPDATE notification_batches SET next_attempt_at=now()+interval '30 seconds',completed_at=CASE WHEN NOT EXISTS(SELECT 1 FROM notification_deliveries WHERE batch_id=$1 AND accepted_at IS NULL) THEN now() ELSE NULL END WHERE id=$1", batch["id"])
		return false
	})
	if err != nil {
		slog.Warn("notification delivery will be retried")
	}
	return false
}
