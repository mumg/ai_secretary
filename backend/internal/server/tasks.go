package server

import (
	"crypto/sha256"
	"fmt"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/domain"
)

func stringsArray(v any) []string {
	out := []string{}
	if a, ok := v.([]string); ok {
		return a
	}
	if a, ok := v.([]any); ok {
		for _, x := range a {
			if s, ok := x.(string); ok {
				out = append(out, s)
			}
		}
	}
	return out
}
func (q *request) normalizeDue(v any) any {
	if v == nil {
		return nil
	}
	due := timestamp(v)
	if due == nil {
		fail(422, "Invalid due_at")
	}
	settings := q.settings()
	loc := must(time.LoadLocation(str(obj(settings, "server"), "timezone")))
	cal := obj(settings, "calendar")
	return must(domain.NormalizeDue(*due, loc, str(cal, "country"), stringsArray(cal["working_dates"]), stringsArray(cal["non_working_dates"])))
}
func (q *request) taskRead(task M) M {
	task["reminders"] = []M{}
	for _, r := range q.rows("SELECT * FROM reminders WHERE task_id=$1 ORDER BY remind_at,id", task["id"]) {
		task["reminders"] = append(task["reminders"].([]M), project("ReminderRead", r))
	}
	return project("TaskRead", task)
}
func (q *request) rerank() {
	q.exec("SELECT pg_advisory_xact_lock(726941831)")
	now := q.now()
	for _, t := range q.rows("SELECT * FROM tasks WHERE status IN ('NEW','IN_PROGRESS','POSSIBLY_COMPLETED')") {
		score, reasons := domain.Rank(str(t, "priority"), str(t, "status"), timestamp(t["due_at"]), now)
		q.update("tasks", t["id"], M{"ranking_score": score, "ranking_reasons": reasons})
	}
}
func (q *request) rebuildPlan() M {
	// Serialize rebuilding today's shared plan across concurrent API and worker writes.
	q.exec("SELECT pg_advisory_xact_lock(726941831)")
	q.rerank()
	now := q.now()
	date := now.Format("2006-01-02")
	rows := q.rows("SELECT * FROM daily_plans WHERE plan_date=$1", date)
	var plan M
	if len(rows) == 0 {
		plan = q.insert("daily_plans", M{"plan_date": date, "generated_at": now})
	} else {
		plan = q.update("daily_plans", rows[0]["id"], M{"generated_at": now})
		q.exec("DELETE FROM daily_plan_items WHERE plan_id=$1", plan["id"])
	}
	for i, t := range q.rows("SELECT id FROM tasks WHERE status IN ('NEW','IN_PROGRESS','POSSIBLY_COMPLETED') ORDER BY ranking_score DESC,due_at NULLS LAST,id") {
		q.insert("daily_plan_items", M{"plan_id": plan["id"], "task_id": t["id"], "position": i + 1})
	}
	return plan
}
func validateTask(m M, creating bool) {
	textField(m, "title", 1, 500, creating)
	if _, ok := m["title"]; ok {
		m["title"] = clean(str(m, "title"))
		if m["title"] == "" {
			fail(422, "Empty title")
		}
	}
	enum(m, "priority", "LOW", "NORMAL", "HIGH", "CRITICAL")
	enum(m, "status", "NEEDS_CONFIRMATION", "NEW", "IN_PROGRESS", "POSSIBLY_COMPLETED", "COMPLETED", "CANCELLED")
	dateField(m, "due_at", false)
}
func pick(m M, keys ...string) M {
	r := M{}
	for _, k := range keys {
		if v, ok := m[k]; ok {
			r[k] = v
		}
	}
	return r
}
func searchClause(table, parameter string) string {
	return table + ".search_vector @@ (websearch_to_tsquery('russian'," + parameter + ") || websearch_to_tsquery('simple'," + parameter + "))"
}

func (s *Server) taskRoutes() {
	s.route("GET /api/v1/tasks", true, func(q *request) any {
		q.rerank()
		query := clean(q.r.URL.Query().Get("q"))
		if len([]rune(query)) > 200 {
			fail(422, "Query too long")
		}
		order := q.r.URL.Query().Get("order")
		sortSQL := "ranking_score DESC,due_at NULLS LAST"
		if order == "due" {
			sortSQL = "CASE priority WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'NORMAL' THEN 2 ELSE 1 END DESC,due_at NULLS LAST"
		} else if order != "" && order != "rank" {
			fail(422, "Invalid order")
		}
		archive := q.r.URL.Query().Get("archive")
		if archive != "" && archive != "true" && archive != "1" {
			fail(422, "Invalid archive filter")
		}
		where := "status NOT IN ('COMPLETED','CANCELLED')"
		if archive != "" {
			where = "status IN ('COMPLETED','CANCELLED')"
			sortSQL = "completed_at DESC NULLS LAST,updated_at DESC,id"
		} else if q.r.URL.Query().Get("include_closed") == "true" || q.r.URL.Query().Get("include_closed") == "1" {
			where = "TRUE"
		}
		result := []M{}
		for _, t := range q.rows("SELECT * FROM tasks WHERE "+where+" AND ($1='' OR "+searchClause("tasks", "$1")+") ORDER BY "+sortSQL, query) {
			result = append(result, q.taskRead(t))
		}
		return result
	})
	s.route("POST /api/v1/tasks", true, func(q *request) any {
		m := q.body()
		validateTask(m, true)
		v := pick(m, "title", "description", "priority")
		v["due_at"] = q.normalizeDue(m["due_at"])
		v["manually_created"] = true
		t := q.insert("tasks", v)
		q.rebuildPlan()
		q.status = 201
		return q.taskRead(q.get("tasks", t["id"]))
	})
	s.route("GET /api/v1/tasks/{id}", false, func(q *request) any {
		t := q.get("tasks", q.id("id"))
		var source any
		if t["source_event_id"] != nil {
			e := q.get("communication_events", t["source_event_id"])
			e["source_label"] = q.get("communication_sources", e["source_id"])["label"]
			source = project("TaskSourceRead", e)
		}
		return M{"task": q.taskRead(t), "source": source}
	})
	s.route("PATCH /api/v1/tasks/{id}", true, func(q *request) any {
		id := q.id("id")
		q.get("tasks", id)
		m := q.body()
		validateTask(m, false)
		v := pick(m, "title", "description", "priority", "status")
		if _, ok := m["priority"]; ok {
			v["priority_source"] = "MANUAL"
		}
		if due, ok := m["due_at"]; ok {
			v["due_at"] = q.normalizeDue(due)
			v["due_reminder_sent_at"] = nil
			v["overdue_notification_date"] = nil
		}
		if status, ok := m["status"]; ok {
			v["completed_at"] = nil
			if status == "COMPLETED" {
				v["completed_at"] = q.now()
			}
		}
		q.update("tasks", id, v)
		q.rebuildPlan()
		return q.taskRead(q.get("tasks", id))
	})
	for _, action := range []string{"complete", "confirm", "reject"} {
		s.route("POST /api/v1/tasks/{id}/"+action, true, func(q *request) any {
			id := q.id("id")
			t := q.get("tasks", id)
			v := M{}
			switch action {
			case "complete":
				v = M{"status": "COMPLETED", "completed_at": q.now()}
			case "confirm":
				if t["status"] != "NEEDS_CONFIRMATION" {
					fail(409, "Task does not require confirmation")
				}
				v["status"] = "NEW"
			case "reject":
				if t["status"] == "COMPLETED" {
					fail(409, "Completed task cannot be rejected")
				}
				v = M{"status": "CANCELLED", "completed_at": nil}
				q.exec("UPDATE reminders SET enabled=false,updated_at=now() WHERE task_id=$1", id)
			}
			q.update("tasks", id, v)
			q.rebuildPlan()
			return q.taskRead(q.get("tasks", id))
		})
	}
	s.route("POST /api/v1/tasks/{id}/reminders", true, func(q *request) any {
		id := q.id("id")
		q.get("tasks", id)
		m := q.body()
		dateField(m, "remind_at", true)
		r := q.insert("reminders", M{"task_id": id, "remind_at": m["remind_at"]})
		q.status = 201
		return project("ReminderRead", r)
	})
	s.route("DELETE /api/v1/tasks/{id}/reminders/{reminder}", true, func(q *request) any {
		id := q.id("id")
		reminder := q.id("reminder")
		r := q.get("reminders", reminder)
		if r["task_id"] != id {
			fail(404, "Reminder not found")
		}
		q.exec("DELETE FROM reminders WHERE id=$1", reminder)
		q.status = 204
		return nil
	})
	s.route("GET /api/v1/plans/today", true, func(q *request) any {
		now := q.now()
		rows := q.rows("SELECT * FROM daily_plans WHERE plan_date=$1", now.Format("2006-01-02"))
		var plan M
		if len(rows) == 0 || q.r.URL.Query().Get("refresh") == "true" {
			plan = q.rebuildPlan()
		} else {
			plan = rows[0]
		}
		items := []M{}
		for _, item := range q.rows("SELECT * FROM daily_plan_items WHERE plan_id=$1 ORDER BY position", plan["id"]) {
			item["task"] = q.taskRead(q.get("tasks", item["task_id"]))
			items = append(items, project("PlanItemRead", item))
		}
		plan["items"] = items
		end := time.Date(now.Year(), now.Month(), now.Day()+1, 0, 0, 0, 0, now.Location())
		meetings := []M{}
		for _, m := range q.rows("SELECT m.*,s.label AS source_label FROM meetings m LEFT JOIN communication_sources s ON s.id=m.source_id WHERE m.starts_at<$1 AND m.ends_at>$2 AND m.status<>'CANCELLED' ORDER BY m.starts_at,m.id", end, now) {
			meetings = append(meetings, project("MeetingRead", m))
		}
		plan["meetings"] = meetings
		return project("DailyPlanRead", plan)
	})
	s.route("PUT /api/v1/devices/current", true, func(q *request) any {
		m := q.body()
		textField(m, "label", 1, 255, true)
		textField(m, "fcm_token", 10, 10000, true)
		if m["language"] == nil {
			m["language"] = "ru"
		}
		enum(m, "language", "ru", "en", "zh")
		q.exec("INSERT INTO devices (id,label,fcm_token,language,active,last_seen_at) VALUES ($1,$2,$3,$4,true,now()) ON CONFLICT(fcm_token) DO UPDATE SET label=EXCLUDED.label,language=EXCLUDED.language,active=true,last_seen_at=now(),updated_at=now()", newID(), m["label"], m["fcm_token"], m["language"])
		return project("DeviceRead", q.one("SELECT * FROM devices WHERE fcm_token=$1", m["fcm_token"]))
	})
	s.route("GET /api/v1/events/{id}", false, func(q *request) any { return project("EventRead", q.get("communication_events", q.id("id"))) })
	s.route("POST /api/v1/events", true, func(q *request) any {
		m := q.body()
		for k, max := range map[string]int{"source_id": 128, "source_type": 32, "external_id": 512} {
			textField(m, k, 1, max, true)
		}
		textField(m, "body", 0, 8<<20, true)
		dateField(m, "occurred_at", true)
		enum(m, "direction", "INCOMING", "OUTGOING", "INTERNAL")
		v := pick(m, "source_id", "source_type", "external_id", "event_type", "direction", "thread_external_id", "subject", "author", "participants", "occurred_at", "body", "source_url")
		if _, ok := v["event_type"]; !ok {
			v["event_type"] = "message"
		}
		digest := sha256.Sum256([]byte(strings.Join([]string{str(m, "source_id"), str(m, "external_id"), str(m, "author"), pythonTimestamp(m["occurred_at"]), str(m, "body")}, "\x00")))
		v["content_hash"] = fmt.Sprintf("%x", digest)
		q.status = 202
		return project("EventRead", q.insert("communication_events", v))
	})
}
