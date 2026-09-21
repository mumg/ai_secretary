package server

import (
	"github.com/mumg/ai_secretary/backend/internal/config"
	"net/mail"
	"strings"
)

func validateRelationships(m M) {
	for _, key := range []string{"managers", "reports"} {
		value, present := m[key]
		if !present {
			continue
		}
		rows, ok := value.([]any)
		if !ok || len(rows) > 200 {
			fail(422, "Relationships must contain at most 200 employees")
		}
		seen := map[string]bool{}
		for _, value := range rows {
			person, ok := value.(map[string]any)
			if !ok {
				fail(422, "Invalid employee")
			}
			textField(person, "name", 1, 200, true)
			person["name"] = clean(str(person, "name"))
			if person["name"] == "" {
				fail(422, "Employee name required")
			}
			emails, ok := person["emails"].([]any)
			if !ok || len(emails) == 0 || len(emails) > 20 {
				fail(422, "Employee email addresses required")
			}
			for i, v := range emails {
				address, ok := v.(string)
				if !ok {
					fail(422, "Invalid email")
				}
				address = strings.ToLower(strings.TrimSpace(address))
				parsed, e := mail.ParseAddress(address)
				if e != nil || parsed.Address != address || !strings.Contains(address, "@") || seen[address] {
					fail(422, "Invalid or duplicate employee email")
				}
				emails[i] = address
				seen[address] = true
			}
		}
	}
}
func (s *Server) delegationRoutes() {
	s.route("GET /api/v1/relationships", false, func(q *request) any { return obj(q.settings(), "relationships") })
	s.route("PUT /api/v1/relationships", true, func(q *request) any {
		m := q.body()
		validateRelationships(m)
		for _, key := range []string{"managers", "reports"} {
			if _, ok := m[key]; !ok {
				fail(422, "Both relationship lists required")
			}
		}
		q.exec("SELECT pg_advisory_xact_lock(726941833)")
		rows := q.rows("SELECT * FROM system_settings WHERE id=1 FOR UPDATE")
		delta := config.Difference(config.Section(q.server.Config.Defaults(), "relationships"), config.Object(pick(m, "managers", "reports")))
		if len(rows) == 0 {
			q.insert("system_settings", M{"id": 1, "payload": M{"relationships": delta}})
		} else {
			p := obj(rows[0], "payload")
			p["relationships"] = delta
			q.update("system_settings", 1, M{"payload": p})
		}
		return obj(q.settings(), "relationships")
	})
	s.route("GET /api/v1/delegations", false, func(q *request) any {
		p := q.r.URL.Query()
		query := clean(p.Get("q"))
		if len([]rune(query)) > 200 {
			fail(422, "Query too long")
		}
		status := p.Get("status")
		if status != "" {
			enum(M{"status": status}, "status", "ASSIGNED", "IN_PROGRESS", "IN_REVIEW", "COMPLETED", "CANCELLED")
		}
		offset, limit, _ := q.pagination()
		due := p.Get("due")
		if due != "" && due != "overdue" && due != "none" {
			fail(422, "Invalid due filter")
		}
		rows := q.rows(`SELECT * FROM delegations WHERE ($1='' OR `+searchClause("delegations", "$1")+`) AND ($2='' OR assignee_email=$2 OR (assignee_email='' AND assignee_name=$2)) AND ($3='' OR status=$3) AND ($4='' OR ($4='overdue' AND due_at<$5 AND status NOT IN ('COMPLETED','CANCELLED')) OR ($4='none' AND due_at IS NULL)) ORDER BY created_at DESC,id LIMIT $6 OFFSET $7`, query, p.Get("assignee"), status, due, q.now(), limit+1, offset)
		more := len(rows) > limit
		if more {
			rows = rows[:limit]
		}
		recipients := q.rows("SELECT assignee_email,min(assignee_name) AS assignee_name FROM delegations GROUP BY assignee_email,CASE WHEN assignee_email='' THEN assignee_name ELSE '' END ORDER BY min(assignee_name),assignee_email")
		for _, r := range rows {
			delete(r, "search_vector")
		}
		return M{"items": rows, "has_more": more, "recipients": recipients}
	})
	s.route("GET /api/v1/delegations/{id}", false, func(q *request) any {
		d := q.get("delegations", q.id("id"))
		delete(d, "search_vector")
		d["history"] = q.rows("SELECT * FROM delegation_history WHERE delegation_id=$1 ORDER BY created_at,id", d["id"])
		return d
	})
	s.route("PATCH /api/v1/delegations/{id}", true, func(q *request) any {
		id := q.id("id")
		q.get("delegations", id)
		d := q.one("SELECT * FROM delegations WHERE id=$1 FOR UPDATE", id)
		m := q.body()
		textField(m, "status", 1, 32, true)
		enum(m, "status", "ASSIGNED", "IN_PROGRESS", "IN_REVIEW", "COMPLETED", "CANCELLED")
		q.insert("delegation_history", M{"delegation_id": id, "old_status": d["status"], "new_status": m["status"], "actor": "USER", "explanation": "Статус изменён пользователем"})
		result := q.update("delegations", id, M{"status": m["status"], "manual_status_at": q.now()})
		delete(result, "search_vector")
		return result
	})
}

func meetingDelegationSource(event M) bool {
	if event["event_type"] == "meeting_transcript" {
		return true
	}
	signal := obj(obj(event, "analysis_result"), "meeting_result")
	return event["event_type"] == "email" && boolean(signal, "detected") && num(signal, "confidence") >= .78
}

// A model verdict alone is insufficient: the assignment must be grounded in
// the user's own transcript turn (or explicit attribution in meeting minutes).
func meetingAssignmentByUser(event, candidate M, names, addresses []string) bool {
	proof := clean(str(candidate, "assignment_evidence"))
	evidence := clean(str(candidate, "evidence"))
	name, email := clean(str(candidate, "assigner_name")), clean(str(candidate, "assigner_email"))
	identity := name
	if email != "" {
		identity = email
	}
	if len([]rune(proof)) < 8 || evidence == "" || !strings.Contains(clean(str(event, "body")), proof) || ownerIdentity(identity, names, addresses, roster(event)) != "user" {
		return false
	}
	if event["event_type"] == "meeting_transcript" {
		body := str(event, "body")
		headers := patterns["assignment__TRANSCRIPT_TURN"]
		found := false
		for header := matchPattern("assignment__TRANSCRIPT_TURN", body); header != nil; {
			next, _ := headers.FindNextMatch(header)
			end := len([]rune(body))
			if next != nil {
				end = next.Index
			}
			turn := clean(string([]rune(body)[header.Index:end]))
			if strings.Contains(turn, proof) && strings.Contains(turn, evidence) {
				if ownerIdentity(header.GroupByName("speaker").String(), names, addresses, roster(event)) != "user" {
					return false
				}
				found = true
			}
			header = next
		}
		return found
	}
	// The sender/organizer of incoming minutes need not be the assigner.
	return strings.Contains(proof, evidence) && ((name != "" && hasName(proof, name)) || (email != "" && strings.Contains(strings.ToLower(proof), strings.ToLower(email))))
}

func (q *request) analyzeDelegations(event, payload M) {
	meeting := meetingDelegationSource(event)
	if event["event_type"] != "email" && !meeting {
		return
	}
	// Serialize matching and status changes. This also prevents simultaneous replies from creating duplicates.
	q.exec("SELECT pg_advisory_xact_lock(726941832)")
	existing := q.rows(`SELECT d.* FROM delegations d JOIN communication_events e ON e.id=d.source_event_id WHERE (e.source_id=$1 AND (($2::text IS NOT NULL AND e.thread_external_id=$2) OR e.id=$3)) OR ($4 AND EXISTS (
        SELECT 1 FROM meeting_results current_result JOIN meeting_results related ON
        (current_result.calendar_meeting_id IS NOT NULL AND related.calendar_meeting_id=current_result.calendar_meeting_id)
        OR COALESCE(related.parent_result_id,related.id)=COALESCE(current_result.parent_result_id,current_result.id)
        WHERE current_result.source_event_id=$3 AND related.source_event_id=e.id
    )) ORDER BY d.created_at DESC LIMIT 100`, event["source_id"], event["thread_external_id"], event["id"], meeting)
	if event["direction"] != "OUTGOING" && !meeting && len(existing) == 0 {
		return
	}
	for _, d := range existing {
		delete(d, "search_vector")
	}
	input := pick(payload, "current_datetime", "timezone", "workday", "user_identity", "direction", "subject", "author", "participants", "body", "attachments_text", "previous_events_in_thread", "relationships")
	input["existing_delegations"] = existing
	input["event_type"] = event["event_type"]
	input["is_meeting_result"] = meeting
	schema := "DelegationAnalysis"
	if meeting {
		schema = "MeetingDelegationAnalysis"
	}
	result := must(q.llm(str(obj(llmDefinitions, "prompts"), "delegations"), input, schema, nil))
	q.applyDelegationAnalysis(event, result, existing, str(payload, "body"))
}
func (q *request) applyDelegationAnalysis(event, result M, existing []M, body string) {
	values, _ := result["items"].([]any)
	for _, value := range values {
		c, ok := value.(map[string]any)
		if !ok || num(c, "confidence") < 0.85 {
			continue
		}
		evidence := clean(str(c, "evidence"))
		if evidence == "" || !strings.Contains(clean(body), evidence) {
			continue
		}
		if meetingDelegationSource(event) {
			names, addresses := q.identity()
			if !meetingAssignmentByUser(event, c, names, addresses) {
				continue
			}
		}
		var target M
		for _, d := range existing {
			if d["id"] == c["delegation_id"] {
				target = d
				break
			}
		}
		if event["direction"] == "OUTGOING" || meetingDelegationSource(event) {
			// Replies can refer to existing work; only an explicit new assignment creates a record.
			if target != nil {
				if len(q.rows("SELECT id FROM delegation_history WHERE delegation_id=$1 AND source_event_id=$2", target["id"], event["id"])) == 0 {
					at, last := timestamp(event["occurred_at"]), timestamp(target["last_content_event_at"])
					if at != nil && (last == nil || at.After(*last)) {
						changes := M{}
						for _, key := range []string{"description", "expected_result"} {
							if str(c, key) != "" {
								changes[key] = c[key]
							}
						}
						changes["last_content_event_at"] = event["occurred_at"]
						if c["due_at"] != nil {
							changes["due_at"] = q.normalizeDue(c["due_at"])
						}
						q.update("delegations", target["id"], changes)
						q.insert("delegation_history", M{"delegation_id": target["id"], "source_event_id": event["id"], "old_status": target["status"], "new_status": target["status"], "actor": "AI", "explanation": evidence})
					}
				}
				continue
			}
			if str(c, "delegation_id") != "" || c["is_new"] != true || clean(str(c, "title")) == "" {
				continue
			}
			email := strings.ToLower(strings.TrimSpace(str(c, "assignee_email")))
			name := clean(str(c, "assignee_name"))
			if email != "" {
				a, err := mail.ParseAddress(email)
				if err != nil || a.Address != email {
					continue
				}
			}
			for _, group := range []string{"reports", "managers"} {
				people, _ := obj(q.settings(), "relationships")[group].([]any)
				for _, value := range people {
					person, ok := value.(map[string]any)
					if !ok {
						continue
					}
					addresses := stringsArray(person["emails"])
					for _, address := range addresses {
						if email != "" && strings.EqualFold(email, address) {
							email = addresses[0]
							name = str(person, "name")
							break
						}
					}
				}
			}
			ownNames, own := q.identity()
			self := false
			for _, a := range own {
				if strings.EqualFold(a, email) {
					self = true
				}
			}
			if meetingDelegationSource(event) {
				for _, ownName := range ownNames {
					self = self || (name != "" && strings.EqualFold(clean(ownName), name))
				}
				// Meeting attendees are not automatically assignees.
				if name == "" && email == "" {
					continue
				}
			}
			if self {
				continue
			}
			// An unknown employee remains explicitly unassigned instead of guessing from To/Cc.
			duplicate := false
			for _, d := range existing {
				if (strings.EqualFold(str(d, "title"), clean(str(c, "title"))) || (d["source_event_id"] == event["id"] && str(d, "evidence") == evidence)) && str(d, "assignee_email") == email && (email != "" || str(d, "assignee_name") == name) {
					duplicate = true
				}
			}
			if duplicate {
				continue
			}
			d := q.insert("delegations", M{"title": bounded(clean(str(c, "title")), 500), "description": c["description"], "expected_result": c["expected_result"], "assignee_name": name, "assignee_email": email, "due_at": q.normalizeDue(c["due_at"]), "source_event_id": event["id"], "evidence": evidence, "confidence": c["confidence"], "status": "ASSIGNED", "last_content_event_at": event["occurred_at"], "last_status_event_at": event["occurred_at"]})
			q.insert("delegation_history", M{"delegation_id": d["id"], "source_event_id": event["id"], "new_status": "ASSIGNED", "actor": "AI", "explanation": evidence})
			existing = append(existing, d)
			// Replies may already have been processed before this older assignment was discovered.
			q.exec("UPDATE communication_events SET analysis_state='PENDING',next_analysis_at=NULL WHERE source_id=$1 AND thread_external_id=$2 AND direction='INCOMING' AND event_type='email' AND occurred_at>$3 AND analysis_state='COMPLETED' AND NOT is_mailing", event["source_id"], event["thread_external_id"], event["occurred_at"])
		} else if event["direction"] == "INCOMING" && target != nil {
			status := str(c, "status")
			if status != "IN_PROGRESS" && status != "IN_REVIEW" && status != "CANCELLED" {
				continue
			}
			// Never reopen closed assignments or infer acceptance from an employee's report.
			current := q.one("SELECT * FROM delegations WHERE id=$1 FOR UPDATE", target["id"])
			if current["status"] == "COMPLETED" || current["status"] == "CANCELLED" || current["status"] == status {
				continue
			}
			at := timestamp(event["occurred_at"])
			if at == nil {
				continue
			}
			stale := false
			for _, key := range []string{"manual_status_at", "last_status_event_at"} {
				if previous := timestamp(current[key]); previous != nil && !at.After(*previous) {
					stale = true
				}
			}
			if stale || len(q.rows("SELECT id FROM delegation_history WHERE delegation_id=$1 AND source_event_id=$2", target["id"], event["id"])) > 0 {
				continue
			}
			q.insert("delegation_history", M{"delegation_id": target["id"], "source_event_id": event["id"], "old_status": current["status"], "new_status": status, "actor": "AI", "explanation": evidence})
			q.update("delegations", target["id"], M{"status": status, "last_status_event_at": event["occurred_at"]})
		}
	}
}
