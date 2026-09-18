package server

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/mumg/ai_secretary/backend/internal/contracts"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestSubjectCharacterization(t *testing.T) {
	data, err := os.ReadFile("testdata/subjects.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases map[string][]M
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for subject, want := range cases {
		t.Run(subject, func(t *testing.T) {
			got := classifySubject(subjectTitle(subject))
			for i := range got {
				got[i] = pick(got[i], "token", "normalized", "kind", "role", "namespace", "object_type")
			}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("got %#v; want %#v", got, want)
			}
		})
	}
}
func TestSearchIntent(t *testing.T) {
	now := time.Date(2026, 9, 17, 12, 0, 0, 0, time.UTC)
	intent := parseSearch("Найди письма от Петрова за прошлую неделю", now, false)
	if intent.scope != "events" || intent.start == nil || intent.start.Format("2006-01-02") != "2026-09-07" || intent.end.Format("2006-01-02") != "2026-09-14" || len(intent.entity) == 0 {
		t.Fatal(intent)
	}
	intent = parseSearch("Просроченные задачи", now, false)
	if !intent.overdue || intent.scope != "tasks" || len(intent.statuses) != 4 || len(intent.terms) != 0 {
		t.Fatal(intent)
	}
	literal := parseSearch("Планы на завтра", now, true)
	if literal.start != nil {
		t.Fatal(literal)
	}
}
func TestFilterReconciliation(t *testing.T) {
	s := testServer(t)
	call(t, s, "PUT", "/api/v1/external-task-sources/manual", M{"label": "Manual"}, 200)
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "manual", "source_type": "external_tasks", "external_id": "one", "subject": "newsletter", "body": "Реклама", "occurred_at": "2026-09-17T10:00:00Z"}, 202).(map[string]any)
	settings := call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"analysis_filters": M{"stop_words": []string{"реклама"}}}}, 200).(map[string]any)
	if num(obj(settings, "filter_reconciliation"), "skipped") != 1 {
		t.Fatal(settings)
	}
	var state string
	s.Pool.QueryRow(context.Background(), "SELECT analysis_state FROM communication_events WHERE id=$1", event["id"]).Scan(&state)
	if state != "SKIPPED" {
		t.Fatal(state)
	}
	settings = call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"analysis_filters": M{"stop_words": []string{}}}}, 200).(map[string]any)
	if num(obj(settings, "filter_reconciliation"), "requeued") != 1 {
		t.Fatal(settings)
	}
}
func TestMTSReferenceQueryAndAmbiguity(t *testing.T) {
	keys := mtsKeys([]string{"https://mts.mts-link.ru/j/MTC/123?eventSessionId=456"})
	if !keysOverlap(keys, []string{"id:456"}) {
		t.Fatal(keys)
	}
	result := M{"title": "Plan", "starts_at": "2026-09-17T10:00:00Z", "ends_at": "2026-09-17T11:00:00Z", "mts_link_keys": []string{"id:123"}}
	meeting := copyMap(result)
	meeting["status"] = "CONFIRMED"
	if transcriptOverlap(result, meeting) != 1 {
		t.Fatal("missing exact occurrence")
	}
	meeting["starts_at"] = "2026-09-18T10:00:00Z"
	meeting["ends_at"] = "2026-09-18T11:00:00Z"
	if transcriptOverlap(result, meeting) >= 0 {
		t.Fatal("reused room linked to another occurrence")
	}
}
func TestRealtimeOverflowCoalesces(t *testing.T) {
	s := &Server{live: liveState{subscribers: map[chan string]bool{}}}
	ch := make(chan string, 1)
	s.live.subscribers[ch] = true
	s.publish("tasks")
	s.publish("events")
	if <-ch != "all" {
		t.Fatal("lost invalidation")
	}
}

func TestIdentityCharacterization(t *testing.T) {
	data, err := os.ReadFile("testdata/identity.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Event            M
		Names, Addresses []string
		Previous         []M
		Expected         M
	}
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		got := assignmentSignals(c.Event, c.Names, c.Addresses, c.Previous)
		var normalized M
		json.Unmarshal(must(json.Marshal(got)), &normalized)
		if !reflect.DeepEqual(normalized, c.Expected) {
			t.Errorf("case %d: got %#v; want %#v", i, normalized, c.Expected)
		}
	}
}
func TestTranscriptCharacterization(t *testing.T) {
	data, err := os.ReadFile("testdata/transcripts.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Event                     M
		Names, Addresses          []string
		Quote, Evidence, Expected string
	}
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		got := transcriptOwner(c.Event, c.Quote, c.Evidence, c.Names, c.Addresses)
		if got != c.Expected {
			t.Errorf("case %d: got %s; want %s", i, got, c.Expected)
		}
	}
}
func TestTaskOwnerCharacterization(t *testing.T) {
	data, err := os.ReadFile("testdata/owners.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Event              M
		Names, Addresses   []string
		Previous           []M
		Evidence, Expected string
	}
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		got := taskOwner(c.Event, c.Evidence, c.Names, c.Addresses, c.Previous...)
		if got != c.Expected {
			t.Errorf("case %d (%s): got %s; want %s", i, c.Evidence, got, c.Expected)
		}
	}
}

func TestAllDocumentedRoutesRegistered(t *testing.T) {
	s := testServer(t)
	var doc M
	json.Unmarshal(contracts.OpenAPI, &doc)
	for path, operations := range obj(doc, "paths") {
		for method := range operations.(map[string]any) {
			concrete := regexp.MustCompile(`\{[^}]+\}`).ReplaceAllString(path, "00000000-0000-4000-8000-000000000001")
			req := httptest.NewRequest(strings.ToUpper(method), concrete, nil)
			_, pattern := s.mux.Handler(req)
			if pattern == "" {
				t.Errorf("missing %s %s", method, path)
			}
		}
	}
}
func TestExchangeConnectionAndSafeFailure(t *testing.T) {
	s := testServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, password, ok := r.BasicAuth()
		if !ok || user != "me" || password != "test-password" {
			t.Error("missing EWS credentials")
		}
		body, _ := io.ReadAll(r.Body)
		if !strings.Contains(string(body), "GetFolder") {
			t.Error("missing operation")
		}
		w.Header().Set("Content-Type", "text/xml")
		fmt.Fprint(w, `<Envelope><Body><GetFolderResponse><ResponseCode>NoError</ResponseCode></GetFolderResponse></Body></Envelope>`)
	}))
	source := M{"id": "exchange", "label": "Exchange", "source_type": "exchange", "credential": "test-password", "settings": M{"ews_url": upstream.URL, "username": "me", "primary_smtp_address": "me@example.test", "auth_type": "basic"}}
	call(t, s, "POST", "/api/v1/admin/sources", source, 201)
	call(t, s, "POST", "/api/v1/admin/sources/exchange/test", M{}, 200)
	upstream.Close()
	failure := call(t, s, "POST", "/api/v1/admin/sources/exchange/test", M{}, 502).(map[string]any)
	if strings.Contains(str(failure, "detail"), "test-password") {
		t.Fatal("secret in failure")
	}
	var message *string
	s.Pool.QueryRow(context.Background(), "SELECT last_error FROM communication_sources WHERE id='exchange'").Scan(&message)
	if message == nil {
		t.Fatal("source failure not persisted")
	}
}
func TestOpenAIStreamAndReferences(t *testing.T) {
	s := testServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer test-key" {
			t.Error("invalid OpenAI request")
		}
		var body M
		json.NewDecoder(r.Body).Decode(&body)
		if boolean(body, "stream") {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Отчёт готов [E1].\"}}]}\n\ndata: [DONE]\n\n")
			return
		}
		writeJSON(w, 200, M{"choices": []M{{"message": M{"content": `{"reference_ids":["E1"]}`}}}})
	}))
	defer upstream.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "openai", "base_url": upstream.URL, "model": "test"}}, "llm_api_key": "test-key"}, 200)
	call(t, s, "PUT", "/api/v1/external-task-sources/mail", M{"label": "Mail"}, 200)
	call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "external_tasks", "external_id": "one", "body": "Отчёт готов", "subject": "Отчёт", "occurred_at": "2026-09-17T10:00:00Z"}, 202)
	req := httptest.NewRequest("POST", "/api/v1/chat/stream", strings.NewReader(`{"query":"отчёт"}`))
	response := httptest.NewRecorder()
	s.ServeHTTP(response, req)
	if response.Code != 200 || !strings.Contains(response.Body.String(), `"type":"answer_delta"`) || !strings.Contains(response.Body.String(), `"key":"E1"`) || !strings.Contains(response.Body.String(), `"type":"complete"`) {
		t.Fatal(response.Code, response.Body.String())
	}
}
func TestConcurrentTaskUpdates(t *testing.T) {
	s := testServer(t)
	ids := []string{}
	for i := 0; i < 6; i++ {
		task := call(t, s, "POST", "/api/v1/tasks", M{"title": fmt.Sprintf("Task %d", i)}, 201).(map[string]any)
		ids = append(ids, str(task, "id"))
	}
	var wg sync.WaitGroup
	for _, id := range ids {
		wg.Add(1)
		go func(id string) {
			defer wg.Done()
			r := httptest.NewRequest("PATCH", "/api/v1/tasks/"+id, strings.NewReader(`{"status":"IN_PROGRESS"}`))
			w := httptest.NewRecorder()
			s.ServeHTTP(w, r)
			if w.Code != 200 {
				t.Errorf("concurrent update: %d %s", w.Code, w.Body.String())
			}
		}(id)
	}
	wg.Wait()
}
func TestTranscriptCalendarRequiresUniqueTopicMatch(t *testing.T) {
	s := testServer(t)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, M{"message": M{"content": `{"matches":true,"confidence":1,"evidence":"same topic"}`}})
	}))
	defer upstream.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"base_url": upstream.URL, "model": "test"}}}, 200)
	call(t, s, "PUT", "/api/v1/external-task-sources/source", M{"label": "Source"}, 200)
	_, err := s.job(context.Background(), func(q *request) bool {
		invitation := q.insert("communication_events", M{"source_id": "source", "source_type": "exchange", "external_id": "calendar", "event_type": "meeting_invitation", "body": "Plan", "occurred_at": "2026-09-17T09:00:00Z", "content_hash": "invitation"})
		values := M{"source_id": "source", "source_event_id": invitation["id"], "external_uid": "one", "last_event_at": "2026-09-17T09:00:00Z", "title": "Plan", "starts_at": "2026-09-17T10:00:00Z", "ends_at": "2026-09-17T11:00:00Z", "mts_link_keys": []string{"id:123"}}
		meeting := q.insert("meetings", values)
		transcript := q.insert("communication_events", M{"source_id": "source", "source_type": "mts_link", "external_id": "transcript", "event_type": "meeting_transcript", "body": "Plan", "occurred_at": "2026-09-17T11:00:00Z", "content_hash": "transcript", "semantic_summary": "Plan", "analysis_result": M{"summary": "Plan"}})
		result := q.insert("meeting_results", M{"source_id": "source", "source_event_id": transcript["id"], "origin_type": "mts_transcript", "transcript_status": "ready", "title": "Plan", "starts_at": values["starts_at"], "ends_at": values["ends_at"], "mts_link_keys": values["mts_link_keys"]})
		q.linkTranscript(result, transcript, q.calendarCandidates())
		if got := q.get("meeting_results", result["id"]); got["calendar_meeting_id"] != meeting["id"] {
			t.Error("unique occurrence not selected", got)
		}
		values["external_uid"] = "two"
		q.insert("meetings", values)
		q.linkTranscript(result, transcript, q.calendarCandidates())
		if got := q.get("meeting_results", result["id"]); got["calendar_meeting_id"] != nil {
			t.Error("ambiguous occurrences linked", got)
		}
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
}
