package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestWorkerAnalysisAndChat(t *testing.T) {
	s := testServer(t)
	llm := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload M
		if e := json.NewDecoder(r.Body).Decode(&payload); e != nil {
			t.Error(e)
		}
		if payload["model"] != "test" {
			t.Error(payload)
		}
		result := M{"summary": "Поручение подготовить отчёт", "thread_summary": "Подготовить отчёт", "categories": []string{}, "keywords": []string{"отчёт"}, "people": []string{}, "organizations": []string{}, "decisions": []string{}, "agreements": []string{}, "tasks": []M{{"title": "Подготовить отчёт", "description": nil, "priority": "NORMAL", "assignee": "user", "assignee_address": "me@example.test", "confidence": 0.99, "evidence": "Подготовь отчёт", "assignment_evidence": "Подготовь отчёт"}}, "completion_candidates": []any{}, "mailing": M{"detected": false, "confidence": 1, "kind": "not_mailing"}}
		content := string(must(json.Marshal(result)))
		if obj(obj(payload, "format"), "properties")["reference_ids"] != nil {
			content = `{"reference_ids":["E1","T2"]}`
		}
		if payload["format"] == nil {
			content = "Нужно подготовить отчёт [E1]."
		}
		writeJSON(w, 200, M{"message": M{"content": content}})
	}))
	defer llm.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"base_url": llm.URL, "model": "test"}}}, 200)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false, "settings": M{"username": "me@example.test"}}, 201)
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "message-1", "event_type": "email", "body": "Подготовь отчёт", "subject": "Отчёт", "author": "sender@example.test", "participants": []M{{"address": "me@example.test", "role": "to"}}, "occurred_at": "2026-09-17T10:00:00Z"}, 202).(map[string]any)
	if !s.processEvent(context.Background()) {
		var errorText *string
		s.Pool.QueryRow(context.Background(), "SELECT analysis_error FROM communication_events WHERE id=$1", event["id"]).Scan(&errorText)
		t.Fatalf("event was not processed: %v", errorText)
	}
	tasks := call(t, s, "GET", "/api/v1/tasks", nil, 200).([]any)
	if len(tasks) != 1 || tasks[0].(map[string]any)["status"] != "NEW" {
		t.Fatal(tasks)
	}
	threads := call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)
	if len(threads["items"].([]any)) != 1 {
		t.Fatal(threads)
	}
	call(t, s, "GET", "/api/v1/threads/"+str(threads["items"].([]any)[0].(map[string]any), "id"), nil, 200)
	chat := call(t, s, "POST", "/api/v1/chat/requests", M{"query": "отчёт"}, 202).(map[string]any)
	if !s.processChat(context.Background()) {
		t.Fatal("chat was not processed")
	}
	chat = call(t, s, "GET", "/api/v1/chat/requests/"+str(chat, "id"), nil, 200).(map[string]any)
	if chat["status"] != "COMPLETED" || len(chat["references"].([]any)) == 0 {
		t.Fatal(chat)
	}
}
func TestCalendarWorkerAndMIME(t *testing.T) {
	s := testServer(t)
	raw := []byte("From: sender@example.test\r\nTo: me@example.test\r\nSubject: Meeting\r\nMessage-ID: <m1>\r\nDate: Thu, 17 Sep 2026 10:00:00 +0300\r\nMIME-Version: 1.0\r\nContent-Type: text/calendar; method=REQUEST; charset=utf-8\r\n\r\nBEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:meeting-1\r\nDTSTART;TZID=Europe/Moscow:20990917T103000\r\nDTEND;TZID=Europe/Moscow:20990917T113000\r\nSUMMARY:Рабочая встреча\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
	parsed, e := parseMail(raw, "1")
	if e != nil {
		t.Fatal(e)
	}
	if parsed.Event["event_type"] != "meeting_invitation" {
		t.Fatal(parsed.Event)
	}
	source := call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201).(map[string]any)
	_, e = s.job(context.Background(), func(q *request) bool { return q.persistMail(source, "INCOMING", parsed) })
	if e != nil {
		t.Fatal(e)
	}
	if !s.processEvent(context.Background()) {
		t.Fatal("meeting was not processed")
	}
	meetings := call(t, s, "GET", "/api/v1/meetings", nil, 200).(map[string]any)
	items := meetings["items"].([]any)
	if len(items) != 1 {
		t.Fatal(meetings)
	}
	m := items[0].(map[string]any)
	if due := timestamp(m["starts_at"]); due == nil || due.UTC().Hour() != 7 {
		t.Fatal(m)
	}
	ctx := call(t, s, "POST", "/api/v1/meetings/"+str(m, "id")+"/context/refresh", M{}, 200).(map[string]any)
	if ctx["status"] != "PENDING" {
		t.Fatal(ctx)
	}
}
func TestMTSImport(t *testing.T) {
	s := testServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer access" {
			t.Error("missing access token")
		}
		switch r.URL.Path {
		case "/api/eventsessions/schedule":
			writeJSON(w, 200, M{"data": M{"items": []M{{"id": "session1", "activitySessionId": "activity1", "name": "Встреча", "startsAt": "2026-09-10T10:00:00Z", "endsAt": "2026-09-10T11:00:00Z"}}}})
		case "/api/eventsessions/endless":
			writeJSON(w, 200, M{"data": M{"items": []any{}}})
		case "/api/event-sessions/activity-sessions/transcript-states":
			writeJSON(w, 200, M{"data": M{"items": []M{{"eventSessionId": "session1", "activitySessionId": "activity1", "transcriptId": "transcript1", "isPublished": true, "status": "ready"}}}})
		case "/api/transcript/transcript1/details":
			writeJSON(w, 200, M{"data": M{"transcriptName": "Итоги", "createdAt": "2026-09-10T11:01:00Z", "ownerName": "Иван"}})
		case "/api/transcript/transcript1":
			writeJSON(w, 200, M{"data": M{"items": []M{{"nickname": "Иван", "text": "Обсудим план", "dateTime": "2026-09-10T10:05:00Z"}, {"nickname": "Анна", "text": "План согласован", "dateTime": "2026-09-10T10:55:00Z"}}}})
		default:
			t.Error(r.URL.Path)
			w.WriteHeader(404)
		}
	}))
	defer upstream.Close()
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mts", "label": "MTS", "source_type": "mts_link", "settings": M{"base_url": upstream.URL}, "credential": "access"}, 201)
	for i := 0; i < 2; i++ {
		_, e := s.job(context.Background(), func(q *request) bool {
			n := q.mtsSync(q.get("communication_sources", "mts"), false)
			if n != 1-i {
				t.Errorf("import count %d", n)
			}
			return true
		})
		if e != nil {
			t.Fatal(e)
		}
	}
	results := call(t, s, "GET", "/api/v1/meeting-results", nil, 200).(map[string]any)
	if len(results["items"].([]any)) != 1 {
		t.Fatal(results)
	}
}
func TestCalendarQuotedTimezone(t *testing.T) {
	calendar := parseCalendar("BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nUID:x\nDTSTART;TZID=\"(UTC+03:00) Example Time\":20260911T120000\nEND:VEVENT\nEND:VCALENDAR", "")
	if tstamp := timestamp(calendar["starts_at"]); tstamp == nil || tstamp.UTC().Hour() != 9 {
		t.Fatal(calendar)
	}
	if parseCalendar("BEGIN:VCALENDAR\nMETHOD:REPLY\nBEGIN:VEVENT\nUID:x\nDTSTART:20260911T120000Z\nEND:VEVENT\nEND:VCALENDAR", "") != nil {
		t.Fatal("calendar reply became a meeting")
	}
}
func TestLLMFailureIsRetriedWithoutLosingEvent(t *testing.T) {
	s := testServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 400, M{"error": "secret-provider-error"}) }))
	defer upstream.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"base_url": upstream.URL}}}, 200)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "retry", "body": "hello", "occurred_at": time.Now().Format(time.RFC3339)}, 202).(map[string]any)
	s.processEvent(context.Background())
	row := must(s.Pool.Query(context.Background(), "SELECT analysis_state,analysis_attempts,analysis_error,next_analysis_at FROM communication_events WHERE id=$1", event["id"]))
	defer row.Close()
	if !row.Next() {
		t.Fatal("event lost")
	}
	var state, detail string
	var attempts int
	var next time.Time
	if e := row.Scan(&state, &attempts, &detail, &next); e != nil {
		t.Fatal(e)
	}
	if state != "PENDING" || attempts != 1 || strings.Contains(detail, "secret-provider-error") || !strings.Contains(detail, "HTTP 400") || !next.After(time.Now()) {
		t.Fatal(state, attempts, detail, next)
	}
}

func TestAbandonedEventClaimRecovered(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "abandoned", "body": "hello", "occurred_at": time.Now().Format(time.RFC3339)}, 202).(map[string]any)
	_, err := s.Pool.Exec(context.Background(), "UPDATE communication_events SET analysis_state='PROCESSING',analysis_attempts=2,updated_at=now()-interval '16 minutes',raw_headers=$2 WHERE id=$1", event["id"], `{"Auto-Submitted":"auto-replied"}`)
	if err != nil {
		t.Fatal(err)
	}
	if !s.processEvent(context.Background()) {
		t.Fatal("abandoned claim not recovered")
	}
	var state string
	var attempts int
	if err := s.Pool.QueryRow(context.Background(), "SELECT analysis_state,analysis_attempts FROM communication_events WHERE id=$1", event["id"]).Scan(&state, &attempts); err != nil {
		t.Fatal(err)
	}
	if state != "IGNORED" || attempts != 3 {
		t.Fatal(state, attempts)
	}
}

func TestRepeatedFailureDoesNotStarveUnattemptedMail(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	ids := []any{}
	for i, name := range []string{"old-failure", "new-mail"} {
		event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": name, "event_type": "email", "body": "Automatic reply", "occurred_at": time.Now().Add(time.Duration(i-2) * time.Hour).Format(time.RFC3339)}, 202).(map[string]any)
		ids = append(ids, event["id"])
		attempts := 0
		if i == 0 {
			attempts = 15
		}
		_, err := s.Pool.Exec(context.Background(), "UPDATE communication_events SET analysis_attempts=$2,next_analysis_at=now()-interval '1 minute',raw_headers=$3 WHERE id=$1", event["id"], attempts, `{"Auto-Submitted":"auto-replied"}`)
		if err != nil {
			t.Fatal(err)
		}
	}
	for _, id := range []any{ids[1], ids[0]} {
		if !s.processEvent(context.Background()) {
			t.Fatal("eligible event was not processed")
		}
		var state string
		if err := s.Pool.QueryRow(context.Background(), "SELECT analysis_state FROM communication_events WHERE id=$1", id).Scan(&state); err != nil {
			t.Fatal(err)
		}
		if state != "IGNORED" {
			t.Fatal("unattempted mail was delayed by repeated failure, or retry was lost", state)
		}
	}
}
