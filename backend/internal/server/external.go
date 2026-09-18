package server

func (s *Server) externalRoutes() {
	s.route("GET /api/v1/external-task-sources", false, func(q *request) any {
		out := []M{}
		for _, m := range q.rows("SELECT * FROM communication_sources WHERE source_type='external_tasks' ORDER BY label") {
			out = append(out, q.sourceRead(m, true))
		}
		return out
	})
	s.route("PUT /api/v1/external-task-sources/{source}", true, func(q *request) any {
		id := q.r.PathValue("source")
		if !sourceID.MatchString(id) {
			fail(422, "Invalid source_id")
		}
		m := q.body()
		m["label"] = clean(str(m, "label"))
		textField(m, "label", 1, 255, true)
		if _, ok := m["enabled"]; !ok {
			m["enabled"] = true
		}
		if _, ok := m["enabled"].(bool); !ok {
			fail(422, "Invalid enabled")
		}
		rows := q.rows("SELECT * FROM communication_sources WHERE id=$1 FOR UPDATE", id)
		var row M
		if len(rows) > 0 {
			if rows[0]["source_type"] != "external_tasks" {
				fail(409, "Source ID belongs to another source type")
			}
			row = q.update("communication_sources", id, M{"label": m["label"], "enabled": m["enabled"], "last_error": nil})
		} else {
			row = q.insert("communication_sources", M{"id": id, "label": m["label"], "enabled": m["enabled"], "source_type": "external_tasks"})
		}
		ids, ok := m["tag_ids"]
		if !ok {
			ids = []any{}
		}
		q.tags(id, ids)
		return q.sourceRead(row, true)
	})
	s.route("POST /api/v1/external-task-sources/{source}/tasks:batch", true, func(q *request) any {
		id := q.r.PathValue("source")
		if !sourceID.MatchString(id) {
			fail(422, "Invalid source_id")
		}
		source := q.one("SELECT * FROM communication_sources WHERE id=$1 AND source_type='external_tasks' FOR UPDATE", id)
		if !boolean(source, "enabled") {
			fail(409, "External task source is disabled")
		}
		body := q.body()
		items, ok := body["tasks"].([]any)
		if !ok {
			if _, present := body["tasks"]; present {
				fail(422, "tasks must be an array")
			}
			items = []any{}
		}
		if len(items) > 500 {
			fail(422, "Too many tasks")
		}
		seen := map[string]bool{}
		result := M{"source_id": id, "created": 0, "updated": 0, "unchanged": 0, "closed_missing": 0, "items": []M{}}
		now := q.now()
		changed := false
		for _, value := range items {
			raw, ok := value.(map[string]any)
			if !ok {
				fail(422, "Invalid task")
			}
			m := M(raw)
			validateTask(m, true)
			m["external_id"] = clean(str(m, "external_id"))
			textField(m, "external_id", 1, 512, true)
			for k, max := range map[string]int{"description": 20000, "source_url": 4000, "evidence": 2000} {
				if m[k] != nil {
					textField(m, k, 0, max, false)
				}
			}
			for _, k := range []string{"occurred_at", "source_updated_at"} {
				dateField(m, k, false)
			}
			if m["status"] == nil {
				m["status"] = "NEW"
			}
			if m["priority"] == nil {
				m["priority"] = "NORMAL"
			}
			external := str(m, "external_id")
			if seen[external] {
				fail(422, "external_id values must be unique within a batch")
			}
			seen[external] = true
			digest := hash(m)
			events := q.rows("SELECT * FROM communication_events WHERE source_id=$1 AND external_id=$2 AND event_type='external_task'", id, external)
			var event, task M
			if len(events) > 0 {
				event = events[0]
				tasks := q.rows("SELECT * FROM tasks WHERE source_event_id=$1 ORDER BY created_at,id LIMIT 1", event["id"])
				if len(tasks) > 0 {
					task = tasks[0]
				}
			}
			stale := false
			if event != nil {
				previous := timestamp(obj(obj(event, "raw_headers"), "External-Task")["source_updated_at"])
				incoming := timestamp(m["source_updated_at"])
				stale = previous != nil && incoming != nil && incoming.Before(*previous)
			}
			action := "unchanged"
			if event == nil || task == nil || (!stale && event["content_hash"] != digest) {
				action = "updated"
				occurred := m["occurred_at"]
				if occurred == nil {
					occurred = now
					if event != nil {
						occurred = event["occurred_at"]
					}
				}
				description := m["description"]
				if description == nil || description == "" {
					description = m["title"]
				}
				ev := M{"subject": m["title"], "body": description, "source_url": m["source_url"], "occurred_at": occurred, "raw_headers": M{"External-Task": M{"source_updated_at": m["source_updated_at"]}}, "content_hash": digest, "analysis_state": "SKIPPED", "analysis_result": M{"external_task": true}, "analysis_error": nil, "analysis_attempts": 0, "next_analysis_at": nil, "semantic_summary": description, "semantic_categories": []any{source["label"]}, "semantic_keywords": []any{}, "semantic_people": []any{}, "semantic_organizations": []any{}, "semantic_decisions": []any{}, "semantic_agreements": []any{}}
				if event == nil {
					action = "created"
					ev["source_id"] = id
					ev["source_type"] = "external_tasks"
					ev["external_id"] = external
					ev["event_type"] = "external_task"
					ev["direction"] = "INTERNAL"
					ev["thread_external_id"] = external
					ev["author"] = source["label"]
					event = q.insert("communication_events", ev)
				} else {
					event = q.update("communication_events", event["id"], ev)
				}
				tv := M{"title": m["title"], "description": m["description"], "status": m["status"], "priority": m["priority"], "priority_source": "SOURCE", "due_at": m["due_at"], "evidence": m["evidence"], "confidence": 1.0, "manually_created": false, "completed_at": nil}
				if m["status"] == "COMPLETED" {
					tv["completed_at"] = now
					if m["source_updated_at"] != nil {
						tv["completed_at"] = m["source_updated_at"]
					}
				}
				if task == nil || !sameTime(task["due_at"], m["due_at"]) {
					tv["due_reminder_sent_at"] = nil
					tv["overdue_notification_date"] = nil
				}
				if task == nil {
					tv["source_event_id"] = event["id"]
					task = q.insert("tasks", tv)
				} else {
					task = q.update("tasks", task["id"], tv)
				}
				changed = true
			}
			result[action] = result[action].(int) + 1
			status := str(task, "status")
			result["items"] = append(result["items"].([]M), M{"external_id": external, "task_id": task["id"], "action": action, "status": status, "active": status != "CANCELLED" && status != "COMPLETED"})
		}
		if boolean(body, "close_missing") {
			for _, t := range q.rows("SELECT t.id,e.external_id FROM tasks t JOIN communication_events e ON e.id=t.source_event_id WHERE e.source_id=$1 AND e.event_type='external_task' AND t.status NOT IN ('COMPLETED','CANCELLED')", id) {
				if !seen[str(t, "external_id")] {
					q.update("tasks", t["id"], M{"status": "CANCELLED", "completed_at": nil})
					result["closed_missing"] = result["closed_missing"].(int) + 1
					changed = true
				}
			}
		}
		if changed {
			q.rebuildPlan()
		}
		return result
	})
}
func sameTime(a, b any) bool {
	first, second := timestamp(a), timestamp(b)
	if first == nil || second == nil {
		return first == nil && second == nil
	}
	return first.Equal(*second)
}
