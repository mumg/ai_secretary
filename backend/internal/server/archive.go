package server

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

const visibleEvent = `e.event_type NOT IN ('meeting_invitation','meeting_transcript') AND e.analysis_state NOT IN ('SKIPPED','IGNORED') AND NOT e.is_mailing`

func page(items []M, offset, limit int) M {
	more := len(items) > limit
	if more {
		items = items[:limit]
	}
	return M{"items": items, "offset": offset, "limit": limit, "has_more": more}
}
func bounded(s string, n int) string {
	if n <= 0 {
		return ""
	}
	r := []rune(s)
	if len(r) > n {
		return string(r[:n-1]) + "…"
	}
	return s
}
func (q *request) pagination() (int, int, string) {
	offset := q.paramInt("offset", 0, 0, 2147483647)
	limit := q.paramInt("limit", 20, 1, 100)
	query := clean(q.r.URL.Query().Get("q"))
	if len([]rune(query)) > 200 {
		fail(422, "Query too long")
	}
	return offset, limit, query
}
func hash(value any) string {
	b := must(json.Marshal(value))
	return fmt.Sprintf("%x", sha256.Sum256(b))
}
func (q *request) meetingContext(id string, refresh bool) M {
	m := q.one("SELECT m.*,s.label AS source_label FROM meetings m LEFT JOIN communication_sources s ON s.id=m.source_id WHERE m.id=$1 FOR UPDATE OF m", id)
	fingerprint := hash([]any{m["title"], m["starts_at"], m["ends_at"], m["status"], m["organizer"], m["attendees"], m["source_event_id"], m["mts_link_keys"]})
	q.exec(`INSERT INTO meeting_contexts (meeting_id,status,"references",meeting_fingerprint) VALUES ($1,'NOT_REQUESTED','[]',$2) ON CONFLICT(meeting_id) DO NOTHING`, id, fingerprint)
	q.exec("UPDATE meeting_contexts SET meeting_fingerprint=$2,status='NOT_REQUESTED',input_fingerprint=NULL,generation=NULL,next_refresh_at=NULL,notify_after=NULL,error=NULL,updated_at=now() WHERE meeting_id=$1 AND meeting_fingerprint<>$2", id, fingerprint)
	ended := timestamp(m["ends_at"]) != nil && !timestamp(m["ends_at"]).After(time.Now())
	cancelled := m["status"] == "CANCELLED"
	if refresh && !ended && !cancelled {
		q.exec("UPDATE meeting_contexts SET requested_at=coalesce(requested_at,now()),status=CASE WHEN status='PROCESSING' THEN status ELSE 'PENDING' END,next_refresh_at=NULL,error=NULL,input_fingerprint=NULL,notify_after=NULL,updated_at=now() WHERE meeting_id=$1", id)
	}
	row := q.one("SELECT * FROM meeting_contexts WHERE meeting_id=$1", id)
	status := row["status"]
	if cancelled {
		status = "CANCELLED"
	} else if ended {
		status = "ENDED"
	}
	return M{"meeting": project("MeetingRead", m), "status": status, "summary": row["summary"], "references": row["references"], "generated_at": row["generated_at"], "error": row["error"], "stale": row["summary"] != nil && row["input_fingerprint"] == nil}
}
func (q *request) resultRead(m M, detail bool) M {
	children := q.rows("SELECT r.*,e.author,e.occurred_at,e.participants,coalesce(s.label,r.source_id) AS source_label FROM meeting_results r JOIN communication_events e ON e.id=r.source_event_id LEFT JOIN communication_sources s ON s.id=r.source_id WHERE parent_result_id=$1 ORDER BY e.occurred_at", m["id"])
	for _, key := range []string{"decisions", "agreements"} {
		values := stringsArray(m[key])
		seen := map[string]bool{}
		out := []string{}
		for _, child := range children {
			values = append(values, stringsArray(child[key])...)
		}
		for _, value := range values {
			normalized := strings.ToLower(clean(value))
			if normalized != "" && !seen[normalized] {
				seen[normalized] = true
				out = append(out, value)
			}
		}
		m[key] = out
	}
	m["supplement_count"] = len(children)
	m["time_known"] = m["origin_type"] != "email_followup" || m["calendar_meeting_id"] != nil
	m["time_basis"] = "session"
	if obj(obj(m, "raw_headers"), "MTS-Link")["time_basis"] == "transcript" {
		m["time_basis"] = "transcript"
	}
	summary := str(m, "summary")
	if summary == "" {
		for _, child := range children {
			if str(child, "summary") != "" {
				summary = str(child, "summary")
				break
			}
		}
	}
	if summary == "" {
		summary = "Резюме не сформировано"
	}
	values := stringsArray(m["agreements"])
	if len(values) == 0 {
		values = stringsArray(m["decisions"])
	}
	if len(values) > 0 {
		summary = strings.Join(values[:min(3, len(values))], " • ")
	}
	m["brief_summary"] = bounded(clean(summary), 360)
	if !detail {
		return project("MeetingResultRead", m)
	}
	participants := []any{}
	if m["calendar_meeting_id"] != nil {
		meeting := q.get("meetings", m["calendar_meeting_id"])
		if meeting["organizer"] != nil {
			participants = append(participants, meeting["organizer"])
		}
		if a, ok := meeting["attendees"].([]any); ok {
			participants = append(participants, a...)
		}
	}
	if a, ok := m["participants"].([]any); ok {
		participants = append(participants, a...)
	}
	for _, c := range children {
		if a, ok := c["participants"].([]any); ok {
			participants = append(participants, a...)
		}
	}
	unique := []any{}
	seen := map[string]bool{}
	for _, p := range participants {
		v, ok := p.(map[string]any)
		if !ok {
			continue
		}
		key := str(v, "address")
		if key == "" {
			key = str(v, "external_id")
		}
		if key == "" {
			key = str(v, "name")
		}
		key = strings.ToLower(key)
		if key != "" && !seen[key] {
			seen[key] = true
			unique = append(unique, p)
		}
	}
	m["participants"] = unique
	summaries := []M{}
	if m["origin_type"] == "email_followup" {
		children = append([]M{m}, children...)
	}
	for _, c := range children {
		if c["origin_type"] == "email_followup" {
			c["occurred_at"] = c["occurred_at"]
			if c["occurred_at"] == nil {
				c["occurred_at"] = c["received_at"]
			}
			if c["summary"] == nil {
				c["summary"] = "Резюме не сформировано"
			}
			summaries = append(summaries, project("ParticipantMeetingSummary", c))
		}
	}
	m["participant_summaries"] = summaries
	return project("MeetingResultDetail", m)
}
func (s *Server) archiveRoutes() {
	s.route("GET /api/v1/threads", false, func(q *request) any {
		offset, limit, query := q.pagination()
		rows := q.rows("SELECT t.*,coalesce(s.label,t.source_id) AS source_label FROM conversation_threads t LEFT JOIN communication_sources s ON s.id=t.source_id WHERE EXISTS (SELECT 1 FROM communication_events e WHERE e.source_id=t.source_id AND e.thread_external_id=t.thread_external_id AND "+visibleEvent+") AND ($1='' OR "+searchClause("t", "$1")+") ORDER BY t.last_event_at DESC,t.id DESC OFFSET $2 LIMIT $3", query, offset, limit+1)
		out := []M{}
		for _, r := range rows {
			out = append(out, project("ConversationThreadRead", r))
		}
		return page(out, offset, limit)
	})
	s.route("GET /api/v1/threads/{id}", false, func(q *request) any {
		m := q.one("SELECT t.*,coalesce(s.label,t.source_id) AS source_label FROM conversation_threads t LEFT JOIN communication_sources s ON s.id=t.source_id WHERE t.id=$1", q.id("id"))
		offset := q.paramInt("events_offset", 0, 0, 2147483647)
		limit := q.paramInt("events_limit", 100, 1, 100)
		condition := "e.source_id=$1 AND e.thread_external_id=$2 AND " + visibleEvent
		events := q.rows("SELECT * FROM communication_events e WHERE "+condition+" ORDER BY e.occurred_at DESC,e.id DESC OFFSET $3 LIMIT $4", m["source_id"], m["thread_external_id"], offset, limit)
		out := []M{}
		for _, e := range events {
			e["preview"] = bounded(str(e, "body"), 1000)
			out = append(out, project("ConversationEventRead", e))
		}
		m["event_count"] = q.one("SELECT count(*) AS n FROM communication_events e WHERE "+condition, m["source_id"], m["thread_external_id"])["n"]
		m["events"] = out
		m["has_more_events"] = num(m, "event_count") > float64(offset+limit)
		m["events_offset"] = offset
		m["events_limit"] = limit
		return project("ConversationThreadDetail", m)
	})
	s.route("GET /api/v1/meetings", false, func(q *request) any {
		offset, limit, query := q.pagination()
		rows := q.rows("SELECT m.*,s.label AS source_label FROM meetings m LEFT JOIN communication_sources s ON s.id=m.source_id WHERE m.ends_at>now() AND m.status<>'CANCELLED' AND ($1='' OR "+searchClause("m", "$1")+") ORDER BY m.starts_at,m.id OFFSET $2 LIMIT $3", query, offset, limit+1)
		out := []M{}
		for _, m := range rows {
			out = append(out, project("MeetingRead", m))
		}
		return page(out, offset, limit)
	})
	s.route("GET /api/v1/meetings/{id}/context", true, func(q *request) any { return q.meetingContext(q.id("id"), false) })
	s.route("POST /api/v1/meetings/{id}/context/refresh", true, func(q *request) any { return q.meetingContext(q.id("id"), true) })
	resultSQL := "SELECT r.*,e.analysis_state,e.raw_headers,e.occurred_at AS received_at,e.author,e.participants,coalesce(s.label,r.source_id) AS source_label FROM meeting_results r JOIN communication_events e ON e.id=r.source_event_id LEFT JOIN communication_sources s ON s.id=r.source_id WHERE r.parent_result_id IS NULL AND r.starts_at<=now()"
	s.route("GET /api/v1/meeting-results", false, func(q *request) any {
		offset, limit, query := q.pagination()
		rows := q.rows(resultSQL+" AND ($1='' OR "+searchClause("r", "$1")+" OR EXISTS(SELECT 1 FROM meeting_results c WHERE c.parent_result_id=r.id AND "+searchClause("c", "$1")+")) ORDER BY r.starts_at DESC,r.id DESC OFFSET $2 LIMIT $3", query, offset, limit+1)
		out := []M{}
		for _, r := range rows {
			out = append(out, q.resultRead(r, false))
		}
		return page(out, offset, limit)
	})
	s.route("GET /api/v1/meeting-results/{id}", false, func(q *request) any { return q.resultRead(q.one(resultSQL+" AND r.id=$1", q.id("id")), true) })
}
