package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestMTSSyncClearsErrorAfterRecovery(t *testing.T) {
	s := testServer(t)
	var recovered atomic.Bool
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !recovered.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		writeJSON(w, http.StatusOK, []any{})
	}))
	defer upstream.Close()
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mts", "label": "MTS", "source_type": "mts_link", "settings": M{"base_url": upstream.URL}, "credential": "test-token"}, 201)
	s.syncSources(context.Background())
	var status string
	var sourceError, message *string
	read := func() {
		t.Helper()
		if err := s.Pool.QueryRow(context.Background(), "SELECT s.last_error,c.status,c.message FROM communication_sources s JOIN component_statuses c ON c.id='source-'||s.id WHERE s.id='mts'").Scan(&sourceError, &status, &message); err != nil {
			t.Fatal(err)
		}
	}
	read()
	if status != "ERROR" || sourceError == nil || message == nil {
		t.Fatal("failed sync did not publish an error", status, sourceError, message)
	}
	// Allow the scheduled retry, then simulate the provider recovering.
	if _, err := s.Pool.Exec(context.Background(), "UPDATE component_statuses SET observed_at=now()-interval '1 day' WHERE id='source-mts'"); err != nil {
		t.Fatal(err)
	}
	recovered.Store(true)
	s.syncSources(context.Background())
	read()
	if status != "OK" || sourceError != nil || message != nil {
		t.Fatal("successful sync retained the previous error", status, sourceError, message)
	}
}

func TestMTSFailuresDescribeCauseWithoutExposingResponse(t *testing.T) {
	for _, tc := range []struct {
		name       string
		status     int
		body, want string
	}{
		{"authorization", 401, `{"error":"private-token"}`, "SSO"},
		{"server", 503, `{"error":"private-token"}`, "HTTP 503"},
		{"invalid-json", 200, `private-token`, "JSON"},
		{"scalar", 200, `true`, "формат"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := testServer(t)
			model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(tc.body))
			}))
			defer model.Close()
			call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mts", "label": "MTS", "source_type": "mts_link", "settings": M{"base_url": model.URL}, "credential": "private-token"}, 201)
			result := call(t, s, "POST", "/api/v1/admin/sources/mts/test", nil, 502).(map[string]any)
			if !strings.Contains(str(result, "detail"), tc.want) || strings.Contains(str(result, "detail"), "private") {
				t.Fatal(result)
			}
			// Exercise the background transaction boundary and persisted status too.
			_, err := s.Pool.Exec(context.Background(), "UPDATE communication_sources SET last_error=NULL WHERE id='mts'")
			if err != nil {
				t.Fatal(err)
			}
			s.syncSources(context.Background())
			var message string
			if err = s.Pool.QueryRow(context.Background(), "SELECT last_error FROM communication_sources WHERE id='mts'").Scan(&message); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(message, tc.want) || strings.Contains(message, "private") {
				t.Fatal(message)
			}
			_, err = s.job(context.Background(), func(q *request) bool {
				q.mtsTranscript(q.get("communication_sources", "mts"), "test-transcript")
				return true
			})
			if err == nil {
				t.Fatal("transcript download suppressed an authentication/server/format error")
			}
		})
	}
}

func TestMTSArrayResponseAfterTokenRefresh(t *testing.T) {
	s := testServer(t)
	refreshed := false
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/accountUcaas/AccountUcaas.Refresh":
			var body M
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Error(err)
			}
			if body["refreshToken"] != "old-refresh" {
				t.Error("refresh credential missing")
			}
			refreshed = true
			writeJSON(w, 200, M{"type": "Tokens", "value": M{"accessToken": "new-access", "refreshToken": "new-refresh"}})
		default:
			if r.Header.Get("Authorization") == "Bearer old-access" {
				w.WriteHeader(401)
				return
			}
			if r.Header.Get("Authorization") != "Bearer new-access" || r.Header.Get("Cookie") != "access=new-access" {
				t.Error("new credentials missing")
			}
			writeJSON(w, 200, []any{})
		}
	}))
	defer upstream.Close()
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mts", "label": "MTS", "source_type": "mts_link", "settings": M{"base_url": upstream.URL}, "credential": tokenPrefix + `{"access_token":"old-access","refresh_token":"old-refresh"}`}, 201)
	s.syncSources(context.Background())
	_, err := s.job(context.Background(), func(q *request) bool {
		row := q.get("communication_sources", "mts")
		access, refresh := q.sourceTokens(row)
		if access != "new-access" || refresh != "new-refresh" || row["last_error"] != nil || row["last_sync_at"] == nil {
			t.Error("refresh or sync state was not preserved")
		}
		return false
	})
	if err != nil || !refreshed {
		t.Fatal("refresh failed", err)
	}
}

func TestSSOResponseStillRequiresObject(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, []any{}) }))
	defer upstream.Close()
	_, status, err := requestJSON(context.Background(), "POST", upstream.URL, nil, nil)
	if status != 200 || err == nil {
		t.Fatal("SSO accepted an array", status, err)
	}
}
