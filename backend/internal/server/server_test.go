package server

import (
	"bytes"
	"context"
	"encoding/json"
	"github.com/mumg/ai_secretary/backend/internal/contracts"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/mumg/ai_secretary/backend/internal/config"
	"github.com/mumg/ai_secretary/backend/internal/store"
)

func testServer(t *testing.T) *Server {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set TEST_DATABASE_URL to run PostgreSQL integration tests")
	}
	ctx := context.Background()
	admin, e := pgxpool.New(ctx, dsn)
	if e != nil {
		t.Fatal(e)
	}
	schema := "go_test_" + strings.ReplaceAll(store.UUID(), "-", "")
	if _, e = admin.Exec(ctx, "CREATE SCHEMA "+store.Quote(schema)); e != nil {
		t.Fatal(e)
	}
	cfg, e := pgxpool.ParseConfig(dsn)
	if e != nil {
		t.Fatal(e)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema + ",public"
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() { pool.Close(); admin.Exec(ctx, "DROP SCHEMA "+store.Quote(schema)+" CASCADE"); admin.Close() })
	if e = store.Migrate(ctx, pool); e != nil {
		t.Fatal(e)
	}
	if e = store.Migrate(ctx, pool); e != nil {
		t.Fatal("migration replay:", e)
	}
	secret := filepath.Join(t.TempDir(), "key")
	if e = os.WriteFile(secret, []byte(strings.Repeat("test-secret-", 4)), 0600); e != nil {
		t.Fatal(e)
	}
	return New(pool, config.Config{MasterKeyFile: secret, PublicURL: "https://localhost", LLMURL: "http://127.0.0.1:11434", WebDir: "../../web"}, "0.1.11")
}
func call(t *testing.T, s *Server, method, path string, body any, status int) any {
	t.Helper()
	data, e := json.Marshal(body)
	if e != nil {
		t.Fatal(e)
	}
	r := httptest.NewRequest(method, path, bytes.NewReader(data))
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != status {
		t.Fatalf("%s %s: got %d, want %d: %s", method, path, w.Code, status, w.Body.String())
	}
	if status == 204 {
		return nil
	}
	var result any
	if e = json.Unmarshal(w.Body.Bytes(), &result); e != nil {
		t.Fatal(e, w.Body.String())
	}
	if err := contracts.ValidateResponse(r.Pattern, strconv.Itoa(status), result); status < 400 && err != nil {
		t.Fatalf("response contract %s: %v", r.Pattern, err)
	}
	return result
}
func TestTaskLifecycle(t *testing.T) {
	s := testServer(t)
	created := call(t, s, "POST", "/api/v1/tasks", M{"title": "  Купить   молоко ", "priority": "HIGH", "due_at": "2026-09-19T15:00:00+03:00"}, 201).(map[string]any)
	id := str(created, "id")
	if created["title"] != "Купить молоко" {
		t.Fatal(created)
	}
	if due := timestamp(created["due_at"]); due == nil || due.In(time.FixedZone("Moscow", 10800)).Day() != 18 {
		t.Fatal("weekend due", created)
	}
	if created["reminders"] == nil {
		t.Fatal("nil reminders")
	}
	reminder := call(t, s, "POST", "/api/v1/tasks/"+id+"/reminders", M{"remind_at": "2026-09-18T10:00:00Z"}, 201).(map[string]any)
	call(t, s, "POST", "/api/v1/tasks/"+id+"/confirm", M{}, 409)
	call(t, s, "POST", "/api/v1/tasks/"+id+"/reject", M{}, 200)
	detail := call(t, s, "GET", "/api/v1/tasks/"+id, nil, 200).(map[string]any)
	task := obj(detail, "task")
	if task["status"] != "CANCELLED" || task["reminders"].([]any)[0].(map[string]any)["enabled"] != false {
		t.Fatal(task)
	}
	call(t, s, "DELETE", "/api/v1/tasks/"+id+"/reminders/"+str(reminder, "id"), nil, 204)
	call(t, s, "PATCH", "/api/v1/tasks/"+id, M{"status": "NEW", "due_at": nil}, 200)
	call(t, s, "GET", "/api/v1/plans/today", nil, 200)
	call(t, s, "POST", "/api/v1/tasks/"+id+"/complete", M{}, 200)
	call(t, s, "POST", "/api/v1/tasks/"+id+"/reject", M{}, 409)
	active := call(t, s, "GET", "/api/v1/tasks", nil, 200).([]any)
	if len(active) != 0 {
		t.Fatal(active)
	}
	closed := call(t, s, "GET", "/api/v1/tasks?include_closed=true&q=молоко", nil, 200).([]any)
	if len(closed) != 1 {
		t.Fatal(closed)
	}
	call(t, s, "POST", "/api/v1/tasks", M{"title": "bad", "priority": "INVALID"}, 422)
	call(t, s, "POST", "/api/v1/tasks", M{"title": "   "}, 422)
}
func TestExternalBatchAtomicAndIdempotent(t *testing.T) {
	s := testServer(t)
	call(t, s, "PUT", "/api/v1/external-task-sources/test", M{"label": "Test"}, 200)
	body := M{"tasks": []M{{"external_id": "one", "title": "First", "source_updated_at": "2026-09-17T10:00:00Z"}}}
	first := call(t, s, "POST", "/api/v1/external-task-sources/test/tasks:batch", body, 200).(map[string]any)
	if num(first, "created") != 1 {
		t.Fatal(first)
	}
	second := call(t, s, "POST", "/api/v1/external-task-sources/test/tasks:batch", body, 200).(map[string]any)
	if num(second, "unchanged") != 1 {
		t.Fatal(second)
	}
	bad := M{"tasks": []M{{"external_id": "two", "title": "Second"}, {"external_id": "two", "title": "Duplicate"}}}
	call(t, s, "POST", "/api/v1/external-task-sources/test/tasks:batch", bad, 422)
	var count int
	if e := s.Pool.QueryRow(context.Background(), "SELECT count(*) FROM tasks").Scan(&count); e != nil || count != 1 {
		t.Fatal(count, e)
	}
	closed := call(t, s, "POST", "/api/v1/external-task-sources/test/tasks:batch", M{"tasks": []any{}, "close_missing": true}, 200).(map[string]any)
	if num(closed, "closed_missing") != 1 {
		t.Fatal(closed)
	}
}
func TestSourcesSettingsAndReadRoutes(t *testing.T) {
	s := testServer(t)
	tag := call(t, s, "POST", "/api/v1/admin/tags", M{"name": "  Работа  "}, 201).(map[string]any)
	call(t, s, "POST", "/api/v1/admin/tags", M{"name": "работа"}, 409)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mailbox", "source_type": "imap", "enabled": false, "credential": "a-password", "tag_ids": []any{tag["id"]}}, 201)
	sources := call(t, s, "GET", "/api/v1/admin/sources", nil, 200).([]any)
	if strings.Contains(string(must(json.Marshal(sources))), "a-password") {
		t.Fatal("credential leaked")
	}
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"model": "test-model"}}, "llm_api_key": "test-api-key"}, 200)
	settings := call(t, s, "GET", "/api/v1/admin/settings", nil, 200).(map[string]any)
	if settings["llm_api_key_configured"] != true || strings.Contains(string(must(json.Marshal(settings))), "test-api-key") {
		t.Fatal(settings)
	}
	for _, path := range []string{"/health/live", "/health/ready", "/api/v1/ui/config", "/api/v1/threads", "/api/v1/meetings", "/api/v1/meeting-results", "/api/v1/chat/requests", "/api/v1/system/status", "/api/v1/system/version"} {
		call(t, s, "GET", path, nil, 200)
	}
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "first", "body": "Test body", "occurred_at": "2026-09-17T10:00:00Z"}, 202).(map[string]any)
	call(t, s, "GET", "/api/v1/events/"+str(event, "id"), nil, 200)
	call(t, s, "DELETE", "/api/v1/admin/sources/mail", nil, 204)
	call(t, s, "GET", "/api/v1/events/"+str(event, "id"), nil, 404)
}
func TestOriginProtection(t *testing.T) {
	for _, origin := range []string{"https://evil.example", "null", "https://user@example.com", "https://example.com.evil"} {
		if AllowedOrigin(origin, "example.com", true) {
			t.Errorf("allowed %q", origin)
		}
	}
	for _, origin := range []string{"", "https://example.com", "http://EXAMPLE.COM/"} {
		if !AllowedOrigin(origin, "example.com", true) {
			t.Errorf("rejected %q", origin)
		}
	}
	s := New(nil, config.Config{LocalOnly: true}, "test")
	r := httptest.NewRequest(http.MethodPost, "http://localhost/api/v1/tasks", nil)
	r.Header.Set("Origin", "https://evil.example")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal(w.Code)
	}
	r = httptest.NewRequest(http.MethodGet, "http://evil.example/health/live", nil)
	w = httptest.NewRecorder()
	s.ServeHTTP(w, r)
	if w.Code != 400 {
		t.Fatal(w.Code)
	}
}
