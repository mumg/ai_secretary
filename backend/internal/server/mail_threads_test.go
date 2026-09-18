package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func threadTestMail(t *testing.T, id, references string) parsedMail {
	t.Helper()
	raw := "From: Sender <sender@example.test>\r\nTo: me@example.test\r\nSubject: Re: Report\r\nMessage-ID: <" + id + ">\r\nDate: Thu, 17 Sep 2026 10:00:00 +0300\r\n"
	if references != "" {
		raw += "References: <" + references + ">\r\n"
	}
	parsed, err := parseMail([]byte(raw+"Content-Type: text/plain; charset=utf-8\r\n\r\nReport details."), id)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func TestImportedMailVisibleBeforeAnalysis(t *testing.T) {
	s := testServer(t)
	source := call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201).(map[string]any)
	first := threadTestMail(t, "first", "")
	if _, err := s.job(context.Background(), func(q *request) bool { return q.persistMail(source, "INCOMING", first) }); err != nil {
		t.Fatal(err)
	}
	list := call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	if len(list) != 1 {
		t.Fatalf("pending mail is missing: %v", list)
	}
	thread := list[0].(map[string]any)
	if num(thread, "event_count") != 1 || thread["summary"] != nil || thread["summarized_at"] != nil {
		t.Fatal(thread)
	}
	detail := call(t, s, "GET", "/api/v1/threads/"+str(thread, "id"), nil, 200).(map[string]any)
	events := detail["events"].([]any)
	if len(events) != 1 || events[0].(map[string]any)["preview"] != "Report details." {
		t.Fatal(detail)
	}
	var state string
	if err := s.Pool.QueryRow(context.Background(), "SELECT analysis_state FROM communication_events WHERE external_id='<first>'").Scan(&state); err != nil || state != "PENDING" {
		t.Fatalf("mail must remain pending: %s %v", state, err)
	}
	if worked, err := s.job(context.Background(), func(q *request) bool { return q.persistMail(source, "INCOMING", first) }); err != nil || worked {
		t.Fatalf("duplicate import: %v %v", worked, err)
	}
	// A newer pending reply must not erase a previously completed summary.
	summarized := time.Now().UTC().Truncate(time.Second)
	_, err := s.job(context.Background(), func(q *request) bool {
		event := q.one("SELECT * FROM communication_events WHERE external_id='<first>'")
		event = q.update("communication_events", event["id"], M{"semantic_summary": "Existing summary", "analysis_model": "test-model", "analyzed_at": summarized, "analysis_state": "COMPLETED"})
		q.rebuildThread(event, M{})
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
	reply := threadTestMail(t, "reply", "first")
	reply.Event["occurred_at"] = first.Event["occurred_at"].(time.Time).Add(time.Hour)
	if _, err = s.job(context.Background(), func(q *request) bool { return q.persistMail(source, "OUTGOING", reply) }); err != nil {
		t.Fatal(err)
	}
	list = call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	if len(list) != 1 {
		t.Fatal(list)
	}
	updated := list[0].(map[string]any)
	if updated["id"] != thread["id"] || num(updated, "event_count") != 2 || updated["summary"] != "Existing summary" {
		t.Fatal(updated)
	}
	var model string
	if err := s.Pool.QueryRow(context.Background(), "SELECT summary_model FROM conversation_threads WHERE id=$1", updated["id"]).Scan(&model); err != nil || model != "test-model" {
		t.Fatalf("summary model changed: %s %v", model, err)
	}
	if at := timestamp(updated["summarized_at"]); at == nil || !at.Equal(summarized) {
		t.Fatal(updated)
	}
}

func TestBackfillImportedThreadsWithoutLLM(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	_, err := s.job(context.Background(), func(q *request) bool {
		for _, state := range []string{"PENDING", "SKIPPED", "IGNORED"} {
			event := threadTestMail(t, state, "").Event
			event["source_id"] = "mail"
			event["source_type"] = "imap"
			event["direction"] = "INCOMING"
			event["content_hash"] = state
			event["analysis_state"] = state
			q.insert("communication_events", event)
		}
		q.setCursor("mail", "imap_uid:INBOX", "42")
		q.indexMissingThreads()
		q.indexMissingThreads()
		if q.cursor("mail", "imap_uid:INBOX") != "42" {
			t.Fatal("repair changed the mail cursor")
		}
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
	list := call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	if len(list) != 1 || num(list[0].(map[string]any), "event_count") != 1 {
		t.Fatal(list)
	}
	if _, err = s.job(context.Background(), func(q *request) bool {
		q.exec("UPDATE communication_events SET analysis_state='IGNORED' WHERE external_id='<PENDING>'")
		q.rebuildThread(q.one("SELECT * FROM communication_events WHERE external_id='<PENDING>'"), M{})
		return true
	}); err != nil {
		t.Fatal(err)
	}
	list = call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	if len(list) != 0 {
		t.Fatal("ignored thread still visible", list)
	}
}

// Referencing pending mail must remain possible while a slow model request holds
// the analysis transaction. FOR UPDATE would block the thread's latest_event FK.
func TestThreadRepairWhileAnalysisIsRunning(t *testing.T) {
	s := testServer(t)
	entered := make(chan struct{}, 1)
	release := make(chan struct{})
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case entered <- struct{}{}:
		default:
		}
		<-release
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"base_url": model.URL, "model": "test"}}}, 200)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	if _, err := s.job(context.Background(), func(q *request) bool {
		event := threadTestMail(t, "slow", "").Event
		event["source_id"] = "mail"
		event["source_type"] = "imap"
		event["direction"] = "INCOMING"
		event["content_hash"] = "slow"
		q.insert("communication_events", event)
		return true
	}); err != nil {
		model.Close()
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	finished := make(chan struct{})
	go func() { defer close(finished); s.processEvent(ctx) }()
	defer func() { cancel(); close(release); <-finished; model.Close() }()
	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("analysis did not reach the model")
	}
	if _, err := s.job(context.Background(), func(q *request) bool {
		status := obj(q.processingStatus(), "metrics")
		if num(status, "events_processing") != 1 || num(status, "events_pending") != 0 {
			t.Errorf("active analysis is not visible: %v", status)
		}
		return true
	}); err != nil {
		t.Fatal(err)
	}
	if s.processEvent(context.Background()) {
		t.Fatal("another worker claimed active analysis")
	}
	repairCtx, stop := context.WithTimeout(context.Background(), 2*time.Second)
	defer stop()
	if _, err := s.job(repairCtx, func(q *request) bool { q.indexMissingThreads(); return true }); err != nil {
		t.Fatalf("model call blocked archive repair: %v", err)
	}
	threads := call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	if len(threads) != 1 {
		t.Fatal("pending mail is not visible during analysis", threads)
	}
}
