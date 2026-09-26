package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/mumg/ai_secretary/backend/internal/config"
	"github.com/mumg/ai_secretary/backend/internal/contracts"
	"github.com/mumg/ai_secretary/backend/internal/store"
)

type M = store.Record
type Server struct {
	Pool    *pgxpool.Pool
	Config  config.Config
	Version string
	mux     *http.ServeMux
	live    liveState
	gateway gatewayState
	push    notificationTransport
	reports struct {
		sync.Mutex
		drafts map[string]diagnosticDraft
	}
}
type request struct {
	context.Context
	db                      store.DB
	w                       http.ResponseWriter
	r                       *http.Request
	server                  *Server
	status                  int
	notifications           [][2]string
	historicalNotifications bool
}
type apiError struct {
	Status int
	Detail string
}

func (e apiError) Error() string     { return e.Detail }
func fail(status int, detail string) { panic(apiError{status, detail}) }

// abort propagates an error to the transaction boundary; no partial mutation is committed.
func must[T any](value T, e error) T {
	if e != nil {
		panic(e)
	}
	return value
}
func check(e error) {
	if e != nil {
		panic(e)
	}
}
func str(m M, k string) string  { v, _ := m[k].(string); return v }
func num(m M, k string) float64 { return config.Number(config.Object(m), k) }
func obj(m M, k string) M {
	if value, ok := m[k].(M); ok {
		return value
	}
	return M(config.Section(config.Object(m), k))
}
func boolean(m M, k string) bool { v, _ := m[k].(bool); return v }
func clean(s string) string      { return strings.Join(strings.Fields(s), " ") }
func project(name string, m M) M {
	result := contracts.Project(name, m)
	// Older imports stored the wire-format From header. Decode on read so they
	// display correctly without rewriting their content hashes or analysis state.
	if author, ok := result["author"].(string); ok {
		result["author"] = decodeMailHeader(author)
	}
	return result
}
func timestamp(v any) *time.Time {
	if v == nil {
		return nil
	}
	if t, ok := v.(time.Time); ok {
		return &t
	}
	s, ok := v.(string)
	if !ok {
		return nil
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05Z07:00"} {
		if t, e := time.Parse(layout, s); e == nil {
			return &t
		}
	}
	return nil
}
func (q *request) rows(sql string, args ...any) []M {
	sql, args = q.sourceQuery(sql, args)
	return must(store.Rows(q.Context, q.db, sql, args...))
}
func (q *request) one(sql string, args ...any) M {
	sql, args = q.sourceQuery(sql, args)
	return must(store.One(q.Context, q.db, sql, args...))
}
func (q *request) get(table string, id any) M {
	if table == "communication_sources" {
		return q.one("SELECT * FROM communication_sources WHERE id=$1", id)
	}
	r, e := store.Get(q.Context, q.db, table, id)
	if errors.Is(e, pgx.ErrNoRows) {
		fail(404, "Not found")
	}
	return must(r, e)
}
func (q *request) insert(table string, m M) M {
	if table == "tasks" {
		q.exec("SELECT pg_advisory_xact_lock(726941831)")
	}
	row := must(store.Insert(q.Context, q.db, table, m))
	if table == "tasks" && row["status"] != "COMPLETED" && row["status"] != "CANCELLED" {
		kind := "NEW_TASK"
		if row["status"] == "NEEDS_CONFIRMATION" {
			kind = "TASK_CONFIRMATION_REQUIRED"
		} else if row["priority"] == "CRITICAL" {
			kind = "CRITICAL_TASK"
		}
		if !boolean(row, "manually_created") || row["priority"] == "CRITICAL" {
			q.notifications = append(q.notifications, [2]string{kind, str(row, "id")})
		}
	}
	if table == "communication_events" {
		q.exec("UPDATE communication_events SET notification_history=EXISTS (SELECT 1 FROM communication_sources WHERE id=$3 AND $2::timestamptz<=created_at) WHERE id=$1", row["id"], row["occurred_at"], row["source_id"])
	}
	return row
}
func (q *request) update(table string, id any, m M) M {
	if table == "tasks" {
		q.exec("SELECT pg_advisory_xact_lock(726941831)")
	}
	if table == "communication_sources" {
		must(store.Update(q.Context, q.db, table, id, q.sourceOverrides(id, m)))
		return q.get(table, id)
	}
	row := must(store.Update(q.Context, q.db, table, id, m))
	if table == "chat_requests" && m["status"] == "COMPLETED" {
		q.notifications = append(q.notifications, [2]string{"CHAT_RESPONSE_READY", str(row, "id")})
	}
	if table == "tasks" && m["status"] == "POSSIBLY_COMPLETED" {
		q.notifications = append(q.notifications, [2]string{"TASK_POSSIBLY_COMPLETED", str(row, "id")})
	}
	return row
}
func (q *request) exec(sql string, args ...any) { _, e := q.db.Exec(q.Context, sql, args...); check(e) }
func (q *request) id(name string) string {
	s := q.r.PathValue(name)
	if len(s) != 36 {
		fail(422, "Invalid UUID")
	}
	for i, r := range s {
		if i == 8 || i == 13 || i == 18 || i == 23 {
			if r != '-' {
				fail(422, "Invalid UUID")
			}
		} else if !strings.ContainsRune("0123456789abcdefABCDEF", r) {
			fail(422, "Invalid UUID")
		}
	}
	return s
}
func (q *request) body() M {
	defer q.r.Body.Close()
	d := json.NewDecoder(http.MaxBytesReader(q.w, q.r.Body, 8<<20))
	var m M
	if d.Decode(&m) != nil || m == nil {
		fail(422, "Expected JSON object")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		fail(422, "Invalid JSON body")
	}
	if contracts.ValidateRequest(q.r.Pattern, m) != nil {
		fail(422, "Request does not match the API schema")
	}
	return m
}
func textField(m M, k string, min, max int, required bool) {
	v, exists := m[k]
	if !exists {
		if required {
			fail(422, k+" is required")
		}
		return
	}
	s, ok := v.(string)
	if !ok || utf8.RuneCountInString(s) < min || utf8.RuneCountInString(s) > max {
		fail(422, "Invalid "+k)
	}
}
func enum(m M, k string, allowed ...string) {
	if v, ok := m[k]; ok {
		for _, a := range allowed {
			if v == a {
				return
			}
		}
		fail(422, "Invalid "+k)
	}
}
func dateField(m M, k string, required bool) {
	v, ok := m[k]
	if !ok || v == nil {
		if required {
			fail(422, k+" is required")
		}
		return
	}
	if timestamp(v) == nil {
		fail(422, k+" must be an RFC3339 timestamp")
	}
}
func (q *request) paramInt(k string, def, min, max int) int {
	s := q.r.URL.Query().Get(k)
	if s == "" {
		return def
	}
	v, e := strconv.Atoi(s)
	if e != nil || v < min || v > max {
		fail(422, "Invalid "+k)
	}
	return v
}
func (q *request) settings() M {
	p := q.server.Config.Defaults()
	rows := q.rows("SELECT payload FROM system_settings WHERE id=1")
	if len(rows) > 0 {
		config.Merge(p, config.Object(obj(rows[0], "payload")))
	}
	if q.server.Config.LocalOnly {
		config.Section(p, "server")["public_url"] = q.server.Config.PublicURL
	}
	return M(p)
}
func (q *request) now() time.Time {
	loc := must(time.LoadLocation(str(obj(q.settings(), "server"), "timezone")))
	return time.Now().In(loc)
}
func New(pool *pgxpool.Pool, c config.Config, version string) *Server {
	s := &Server{Pool: pool, Config: c, Version: version, mux: http.NewServeMux()}
	s.route("GET /health/live", false, func(q *request) any { return M{"status": "ok", "version": s.Version} })
	s.route("GET /health/ready", false, func(q *request) any {
		r := q.one("SELECT version,revision FROM database_schema_version WHERE id=1")
		if num(r, "version") < store.LatestRevision {
			fail(503, "Database schema is not ready")
		}
		return M{"status": "ready", "database_schema_version": r["version"], "database_schema_revision": r["revision"]}
	})
	s.route("GET /api/v1/ui/config", false, func(q *request) any { return M{"timezone": obj(q.settings(), "server")["timezone"]} })
	s.live.subscribers = map[chan string]bool{}
	s.live.version = M{"current_version": version, "latest_version": nil, "update_available": false, "checked_at": nil, "last_success_at": nil, "next_check_at": nil, "error": nil, "repository_url": "https://github.com/mumg/ai_secretary"}
	s.mux.HandleFunc("GET /openapi.json", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write(contracts.OpenAPI)
	})
	s.routes()
	for _, prefix := range []string{"/app/assets/", "/admin/assets/"} {
		s.mux.Handle("GET "+prefix, http.StripPrefix(prefix, http.FileServer(http.Dir(filepath.Join(c.WebDir, "assets")))))
	}
	for _, p := range []string{"/{$}", "/app", "/app/{$}", "/admin", "/admin/{$}"} {
		file := "app.html"
		if strings.HasPrefix(p, "/admin") {
			file = "index.html"
		}
		s.mux.HandleFunc("GET "+p, func(w http.ResponseWriter, r *http.Request) {
			w.Header().Set("Cache-Control", "no-cache")
			http.ServeFile(w, r, filepath.Join(c.WebDir, file))
		})
	}
	s.mux.HandleFunc("GET /app/reports", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-cache")
		http.ServeFile(w, r, filepath.Join(c.WebDir, "reports.html"))
	})
	s.mux.HandleFunc("GET /app/report/new", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-cache")
		http.ServeFile(w, r, filepath.Join(c.WebDir, "report-new.html"))
	})
	return s
}
func (s *Server) route(pattern string, transaction bool, fn func(*request) any) {
	s.mux.HandleFunc(pattern, func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if v := recover(); v != nil {
				status, detail := 500, "Internal server error"
				switch e := v.(type) {
				case *llmFailure:
					status, detail = 502, e.Error()
				case apiError:
					status, detail = e.Status, e.Detail
				case error:
					if errors.Is(e, pgx.ErrNoRows) {
						status, detail = 404, "Not found"
					}
					var pe *pgconn.PgError
					if errors.As(e, &pe) {
						switch pe.Code {
						case "23505":
							status, detail = 409, "Already exists"
						case "23503", "23502", "22P02", "22001":
							status, detail = 422, "Invalid data"
						}
					}
				}
				if status >= 500 {
					slog.Error("request failed", "method", r.Method, "path", r.URL.Path, "error_type", fmt.Sprintf("%T", v))
				}
				writeJSON(w, status, M{"detail": detail})
			}
		}()
		q := &request{Context: r.Context(), db: s.Pool, w: w, r: r, server: s, status: 200}
		var tx pgx.Tx
		if transaction {
			tx = must(s.Pool.Begin(r.Context()))
			defer tx.Rollback(r.Context())
			q.db = tx
		}
		result := fn(q)
		q.enqueueNotifications()
		if tx != nil {
			check(tx.Commit(r.Context()))
		}
		writeJSON(w, q.status, result)
	})
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	if status == 204 {
		w.WriteHeader(status)
		return
	}
	data, e := json.Marshal(value)
	if e != nil {
		http.Error(w, "Internal server error", 500)
		return
	}
	w.WriteHeader(status)
	_, _ = w.Write(data)
}
func AllowedOrigin(origin, host string, websocket bool) bool {
	if origin == "" {
		return true
	}
	u, e := url.Parse(origin)
	if e != nil || u.User != nil || (u.Scheme != "http" && u.Scheme != "https") || !strings.EqualFold(u.Host, host) {
		return false
	}
	return !websocket || ((u.Path == "" || u.Path == "/") && u.RawQuery == "" && u.Fragment == "")
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if s.Config.LocalOnly {
		host := r.Host
		if h, _, e := net.SplitHostPort(host); e == nil {
			host = h
		}
		if host != "localhost" && host != "127.0.0.1" {
			writeJSON(w, 400, M{"detail": "Invalid host header"})
			return
		}
	}
	if r.Method != "GET" && r.Method != "HEAD" && r.Method != "OPTIONS" {
		site := r.Header.Get("Sec-Fetch-Site")
		if site == "cross-site" || site == "same-site" || !AllowedOrigin(r.Header.Get("Origin"), r.Host, false) {
			writeJSON(w, 403, M{"detail": "Cross-origin write denied"})
			return
		}
	}
	s.mux.ServeHTTP(w, r)
}
