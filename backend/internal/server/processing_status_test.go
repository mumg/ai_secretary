package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestFailedMailRemainsLocalToThread(t *testing.T) {
	s := testServer(t)
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, M{"models": []M{{"name": "test-model"}}})
	}))
	defer model.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "ollama", "base_url": model.URL, "model": "test-model"}}}, 200)
	source := call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201).(map[string]any)
	reason := "Анализ не будет выполнен: ответ модели обрезан по лимиту токенов."
	if _, err := s.job(context.Background(), func(q *request) bool {
		q.persistMail(source, "INCOMING", threadTestMail(t, "failed", ""))
		q.exec("UPDATE communication_events SET analysis_state='FAILED',analysis_error=$1", reason)
		return true
	}); err != nil {
		t.Fatal(err)
	}
	threads := call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	thread := call(t, s, "GET", "/api/v1/threads/"+str(threads[0].(map[string]any), "id"), nil, 200).(map[string]any)
	event := thread["events"].([]any)[0].(map[string]any)
	if event["analysis_error"] != reason {
		t.Fatal("thread lost the failed message's reason", event)
	}
	full := call(t, s, "GET", "/api/v1/events/"+str(event, "id"), nil, 200).(map[string]any)
	if full["analysis_error"] != reason || full["analysis_state"] != "FAILED" {
		t.Fatal("message failure was hidden or reset", full)
	}
	status := call(t, s, "GET", "/api/v1/system/status", nil, 200).(map[string]any)
	if status["overall_status"] != "OK" {
		t.Fatal("message failure degraded system health", status)
	}
	for _, tc := range []struct{ state, age, want string }{
		{"FAILED", "0 minutes", "OK"},
		{"PENDING", "0 minutes", "BUSY"},
		{"PROCESSING", "16 minutes", "ERROR"},
	} {
		if _, err := s.job(context.Background(), func(q *request) bool {
			q.exec("UPDATE communication_events SET analysis_state=$1,updated_at=now()-$2::interval", tc.state, tc.age)
			got := q.processingStatus()
			if got["status"] != tc.want {
				t.Errorf("%s: got %v, want %s", tc.state, got, tc.want)
			}
			if tc.state == "FAILED" && (got["message"] != nil || num(obj(got, "metrics"), "events_failed") != 1) {
				t.Error("failure should remain a diagnostic count without a system warning", got)
			}
			return true
		}); err != nil {
			t.Fatal(err)
		}
	}
}
