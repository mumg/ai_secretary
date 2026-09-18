package server

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/jackc/pgx/v5"
)

type liveState struct {
	mu             sync.Mutex
	versionMu      sync.Mutex
	ready          bool
	subscribers    map[chan string]bool
	version        M
	nextCheck      time.Time
	nextManual     time.Time
	statusMu       sync.Mutex
	statusSnapshot M
	statusAt       time.Time
}

func (s *Server) Start(ctx context.Context) {
	go s.listenChanges(ctx)
	go func() {
		ticker := time.NewTicker(time.Minute)
		defer ticker.Stop()
		for {
			s.checkVersion(ctx, false)
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
}
func (s *Server) publish(topic string) {
	switch topic {
	case "all", "tasks", "meetings", "contexts", "results", "threads", "events", "chat", "status":
	default:
		return
	}
	s.live.mu.Lock()
	defer s.live.mu.Unlock()
	for ch := range s.live.subscribers {
		select {
		case ch <- topic:
		default:
		drain:
			for {
				select {
				case <-ch:
				default:
					break drain
				}
			}
			ch <- "all"
		}
	}
}
func (s *Server) listenChanges(ctx context.Context) {
	for ctx.Err() == nil {
		conn, e := pgx.Connect(ctx, s.Config.DatabaseURL)
		if e == nil {
			_, e = conn.Exec(ctx, "LISTEN improver_changes")
		}
		if e == nil {
			s.live.mu.Lock()
			s.live.ready = true
			s.live.mu.Unlock()
			s.publish("all")
			ticks := 0
			for ctx.Err() == nil {
				wait, cancel := context.WithTimeout(ctx, 20*time.Second)
				notification, err := conn.WaitForNotification(wait)
				cancel()
				if err != nil {
					if ctx.Err() != nil {
						break
					}
					if wait.Err() == context.DeadlineExceeded {
						if e = conn.Ping(ctx); e != nil {
							break
						}
						ticks++
						if ticks%15 == 0 {
							s.publish("all")
						} else {
							s.publish("status")
						}
						continue
					}
					break
				}
				s.publish(notification.Payload)
			}
		}
		s.live.mu.Lock()
		s.live.ready = false
		s.live.mu.Unlock()
		if conn != nil {
			closeCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			conn.Close(closeCtx)
			cancel()
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Second):
		}
	}
}
func (s *Server) realtime(w http.ResponseWriter, r *http.Request) {
	if !AllowedOrigin(r.Header.Get("Origin"), r.Host, true) {
		http.Error(w, "Forbidden", 403)
		return
	}
	s.live.mu.Lock()
	ready := s.live.ready
	s.live.mu.Unlock()
	if !ready {
		http.Error(w, "Realtime unavailable", 503)
		return
	}
	conn, e := websocket.Accept(w, r, &websocket.AcceptOptions{InsecureSkipVerify: true})
	if e != nil {
		return
	}
	defer conn.CloseNow()
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	ch := make(chan string, 16)
	s.live.mu.Lock()
	s.live.subscribers[ch] = true
	s.live.mu.Unlock()
	defer func() { s.live.mu.Lock(); delete(s.live.subscribers, ch); s.live.mu.Unlock() }()
	ch <- "all"
	go func() {
		defer cancel()
		for {
			readCtx, stop := context.WithTimeout(ctx, 65*time.Second)
			kind, data, e := conn.Read(readCtx)
			stop()
			if e != nil {
				return
			}
			if kind != websocket.MessageText || string(data) != "pong" {
				conn.Close(websocket.StatusPolicyViolation, "Expected pong")
				return
			}
		}
	}()
	send := func(value M) error {
		data, e := json.Marshal(value)
		if e != nil {
			return e
		}
		writeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		return conn.Write(writeCtx, websocket.MessageText, data)
	}
	var lastStatus time.Time
	sendStatus := func() error {
		if r.URL.Query().Get("status") != "1" || time.Since(lastStatus) < time.Second {
			return nil
		}
		snapshotCtx, stop := context.WithTimeout(ctx, 5*time.Second)
		defer stop()
		snapshot, err := s.statusSnapshot(snapshotCtx)
		if err != nil {
			return err
		}
		lastStatus = time.Now()
		return send(M{"type": "status", "data": snapshot})
	}
	if sendStatus() != nil {
		return
	}
	statusTicker := time.NewTicker(5 * time.Second)
	defer statusTicker.Stop()
	ticker := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case topic := <-ch:
			pending := map[string]bool{topic: true}
			timer := time.NewTimer(200 * time.Millisecond)
		batch:
			for {
				select {
				case topic = <-ch:
					pending[topic] = true
				case <-timer.C:
					break batch
				case <-ctx.Done():
					timer.Stop()
					return
				}
			}
			topics := []string{}
			for topic := range pending {
				topics = append(topics, topic)
			}
			sort.Strings(topics)
			if sendStatus() != nil {
				return
			}
			if send(M{"type": "changed", "topics": topics}) != nil {
				return
			}
			if send(M{"type": "ping"}) != nil {
				return
			}
		case <-statusTicker.C:
			if sendStatus() != nil {
				return
			}
		case <-ticker.C:
			s.live.mu.Lock()
			ready = s.live.ready
			s.live.mu.Unlock()
			if !ready {
				conn.Close(websocket.StatusTryAgainLater, "Listener reconnecting")
				return
			}
			if send(M{"type": "ping"}) != nil {
				return
			}
		}
	}
}

var versionPattern = regexp.MustCompile(`^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$`)

func versionNewer(a, b string) bool {
	aa, bb := strings.Split(a, "."), strings.Split(b, ".")
	for i := 0; i < 3; i++ {
		av, _ := strconv.Atoi(aa[i])
		bv, _ := strconv.Atoi(bb[i])
		if av != bv {
			return av > bv
		}
	}
	return false
}
func (s *Server) checkVersion(ctx context.Context, manual bool) M {
	s.live.versionMu.Lock()
	defer s.live.versionMu.Unlock()
	now := time.Now().UTC()
	due := s.live.nextCheck
	if manual {
		due = s.live.nextManual
	}
	if now.Before(due) {
		return copyMap(s.live.version)
	}
	s.live.nextManual = now.Add(time.Minute)
	s.live.nextCheck = now.Add(15 * time.Minute)
	v := s.live.version
	v["checked_at"] = now
	v["error"] = "Не удалось связаться с GitHub. Проверка будет повторена."
	req, e := http.NewRequestWithContext(ctx, "GET", "https://raw.githubusercontent.com/mumg/ai_secretary/main/version", nil)
	if e == nil {
		req.Header.Set("User-Agent", "AI-Secretary-Version-Check")
		client := http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
		response, e := client.Do(req)
		if e == nil {
			defer response.Body.Close()
			data, e := io.ReadAll(io.LimitReader(response.Body, 129))
			latest := strings.TrimSpace(string(data))
			if response.StatusCode == 200 && e == nil && len(data) <= 128 && versionPattern.MatchString(latest) && versionPattern.MatchString(s.Version) {
				v["latest_version"] = latest
				v["update_available"] = versionNewer(latest, s.Version)
				v["last_success_at"] = now
				v["error"] = nil
				s.live.nextCheck = now.Add(6 * time.Hour)
			}
		}
	}
	v["next_check_at"] = s.live.nextCheck
	return copyMap(v)
}
func copyMap(m M) M {
	r := M{}
	for k, v := range m {
		r[k] = v
	}
	return r
}
func (q *request) statusRead(row M) M {
	if expires := timestamp(row["expires_at"]); expires != nil && !expires.After(time.Now()) {
		row["status"] = "STALE"
		if row["message"] == nil {
			row["message"] = "Heartbeat не обновлён вовремя"
		}
	}
	return project("ComponentStatusRead", row)
}
func (q *request) component(id string, m M) M {
	rows := q.rows("SELECT * FROM component_statuses WHERE id=$1 FOR UPDATE", id)
	observed := timestamp(m["observed_at"])
	if observed == nil {
		now := time.Now().UTC()
		observed = &now
	}
	if len(rows) > 0 {
		if t := timestamp(rows[0]["observed_at"]); t != nil && t.After(*observed) {
			return project("ComponentStatusRead", rows[0])
		}
	}
	v := pick(m, "label", "component_type", "status", "message", "metrics")
	v["observed_at"] = *observed
	v["expires_at"] = nil
	if ttl, ok := m["ttl_seconds"]; !ok || ttl != nil {
		seconds := num(m, "ttl_seconds")
		if !ok {
			seconds = 300
		}
		v["expires_at"] = observed.Add(time.Duration(seconds) * time.Second)
	}
	var row M
	if len(rows) == 0 {
		v["id"] = id
		row = q.insert("component_statuses", v)
	} else {
		row = q.update("component_statuses", id, v)
	}
	return project("ComponentStatusRead", row)
}
func (s *Server) systemRoutes() {
	s.mux.HandleFunc("GET /api/v1/realtime", s.realtime)
	s.route("GET /api/v1/system/version", false, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		s.live.versionMu.Lock()
		defer s.live.versionMu.Unlock()
		return copyMap(s.live.version)
	})
	s.route("POST /api/v1/system/version/check", false, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		return s.checkVersion(q.Context, true)
	})
	s.route("PUT /api/v1/system/components/{component}", true, func(q *request) any {
		id := q.r.PathValue("component")
		if !sourceID.MatchString(id) {
			fail(422, "Invalid component ID")
		}
		if strings.HasPrefix(id, "source-") || id == "worker-main" || id == "ollama" || id == "processing" || id == "ollama-semaphore" {
			fail(409, "Component ID is reserved")
		}
		m := q.body()
		textField(m, "label", 1, 255, true)
		enum(m, "status", "OK", "BUSY", "DEGRADED", "ERROR", "UNKNOWN")
		if m["status"] == nil {
			fail(422, "status is required")
		}
		if m["component_type"] == nil {
			m["component_type"] = "external"
		}
		enum(m, "component_type", "external", "external_loader", "integration")
		dateField(m, "observed_at", false)
		if v, ok := m["ttl_seconds"]; ok && v != nil {
			if n := num(m, "ttl_seconds"); n < 30 || n > 86400 {
				fail(422, "Invalid TTL")
			}
		}
		for _, v := range obj(m, "metrics") {
			if _, ok := v.(float64); !ok {
				fail(422, "Metrics must be numeric")
			}
		}
		return q.component(id, m)
	})
	s.route("GET /api/v1/system/status", false, func(q *request) any {
		return q.systemStatus()
	})
	s.route("GET /api/v1/admin/status", false, func(q *request) any {
		ollama := "unavailable"
		cfg := q.llmConfig()
		ctx, cancel := context.WithTimeout(q.Context, 3*time.Second)
		defer cancel()
		base := strings.TrimRight(str(cfg, "base_url"), "/")
		path := "/api/version"
		if cfg["provider"] == "openai" {
			if !strings.HasSuffix(base, "/v1") {
				base += "/v1"
			}
			path = "/models"
		}
		req, err := http.NewRequestWithContext(ctx, "GET", base+path, nil)
		if err == nil {
			if key := str(cfg, "api_key"); key != "" {
				req.Header.Set("Authorization", "Bearer "+key)
			}
			response, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
			if err == nil {
				defer response.Body.Close()
				var v M
				if response.StatusCode == 200 && json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&v) == nil {
					ollama = str(v, "version")
					if ollama == "" {
						ollama = "ok"
					}
				}
			}
		}
		return M{"status": "ready", "time": time.Now().UTC(), "tasks": q.one("SELECT count(*) AS n FROM tasks")["n"], "devices": q.one("SELECT count(*) AS n FROM devices WHERE active")["n"], "sources": q.one("SELECT count(*) AS n FROM communication_sources")["n"], "ollama": ollama}
	})
}

func (q *request) systemStatus() M {
	out := []M{}
	for _, r := range q.rows("SELECT * FROM component_statuses ORDER BY id") {
		out = append(out, q.statusRead(r))
	}
	for _, r := range q.rows("SELECT * FROM communication_sources WHERE source_type<>'external_tasks' AND NOT EXISTS(SELECT 1 FROM component_statuses c WHERE c.id='source-'||communication_sources.id)") {
		status := "UNKNOWN"
		if !boolean(r, "enabled") {
			status = "DISABLED"
		} else if r["last_error"] != nil {
			status = "ERROR"
		} else if r["last_sync_at"] != nil {
			status = "OK"
		}
		out = append(out, M{"id": "source-" + str(r, "id"), "label": r["label"], "component_type": "event_loader", "status": status, "message": nil, "metrics": M{}, "observed_at": r["updated_at"], "expires_at": nil})
	}
	out = append(out, q.processingStatus(), q.semaphoreStatus(), q.llmStatus())
	overall := "OK"
	severity := map[string]int{"OK": 0, "DISABLED": 0, "BUSY": 1, "UNKNOWN": 2, "DEGRADED": 3, "STALE": 3, "ERROR": 4}
	for _, c := range out {
		if severity[str(c, "status")] > severity[overall] {
			overall = str(c, "status")
		}
	}
	return M{"overall_status": overall, "generated_at": time.Now().UTC(), "components": out}
}

// Reuse snapshots across sockets; probing model health must not multiply by
// the number of open browser tabs. Every reconnect receives a full snapshot.
func (s *Server) statusSnapshot(ctx context.Context) (M, error) {
	s.live.statusMu.Lock()
	defer s.live.statusMu.Unlock()
	if s.live.statusSnapshot != nil && time.Since(s.live.statusAt) < 2*time.Second {
		return s.live.statusSnapshot, nil
	}
	var snapshot M
	_, err := s.job(ctx, func(q *request) bool { snapshot = q.systemStatus(); return false })
	if err == nil {
		s.live.statusSnapshot = snapshot
		s.live.statusAt = time.Now()
	}
	return snapshot, err
}

func (q *request) processingStatus() M {
	metrics := M{}
	queued, stuck := 0.0, 0.0
	for _, spec := range [][3]string{{"events", "communication_events", "analysis_state"}, {"chat", "chat_requests", "status"}, {"contexts", "meeting_contexts", "status"}} {
		counts := q.rows("SELECT " + spec[2] + " AS status,count(*) AS count FROM " + spec[1] + " GROUP BY " + spec[2])
		for _, state := range []string{"PENDING", "PROCESSING", "FAILED"} {
			count := 0.0
			for _, row := range counts {
				if row["status"] == state {
					count = num(row, "count")
				}
			}
			metrics[spec[0]+"_"+strings.ToLower(state)] = count
			if state != "FAILED" {
				queued += count
			}
		}
		column := "started_at"
		if spec[0] == "events" {
			column = "updated_at"
		}
		stuck += num(q.one("SELECT count(*) AS count FROM "+spec[1]+" WHERE "+spec[2]+"='PROCESSING' AND "+column+"<now()-interval '15 minutes'"), "count")
	}
	stats := q.one(`SELECT count(*) AS events_total,
 count(*) FILTER (WHERE analysis_state='COMPLETED') AS events_completed,
 count(*) FILTER (WHERE analysis_state IN ('IGNORED','SKIPPED')) AS events_excluded,
 count(*) FILTER (WHERE analysis_state='COMPLETED' AND analyzed_at>=now()-interval '15 minutes' AND analyzed_at<=now()) AS events_completed_last_15m
 FROM communication_events`)
	for key, value := range stats {
		metrics[key] = value
	}
	metrics["events_rate_per_minute"] = num(stats, "events_completed_last_15m") / 15
	metrics["events_retry_waiting"] = q.one("SELECT count(*) AS n FROM communication_events WHERE analysis_state='PENDING' AND next_analysis_at>now()")["n"]
	metrics["tasks_active"] = q.one("SELECT count(*) AS n FROM tasks WHERE status NOT IN ('COMPLETED','CANCELLED')")["n"]
	metrics["stuck"] = stuck
	status := "OK"
	var message any
	if stuck > 0 {
		status = "ERROR"
		message = "Есть обработчики без прогресса более 15 минут"
	} else if queued > 0 {
		status = "BUSY"
		message = "Очереди обрабатываются"
	}
	return M{"id": "processing", "label": "Обработка", "component_type": "processing", "status": status, "message": message, "metrics": metrics, "observed_at": time.Now(), "expires_at": nil}
}
func (q *request) semaphoreStatus() M {
	metrics := q.one("SELECT count(*) FILTER(WHERE granted) AS in_use,count(*) FILTER(WHERE NOT granted) AS waiting FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=2026091101")
	metrics["capacity"] = 1
	metrics["interactive_ready"] = q.one("SELECT count(*) AS n FROM chat_requests WHERE status='PENDING' AND (next_attempt_at IS NULL OR next_attempt_at<=now())")["n"]
	status := "OK"
	if num(metrics, "in_use")+num(metrics, "waiting") > 0 {
		status = "BUSY"
	}
	return M{"id": "ollama-semaphore", "label": "Семафор LLM", "component_type": "semaphore", "status": status, "message": nil, "metrics": metrics, "observed_at": time.Now(), "expires_at": nil}
}
func (q *request) llmStatus() M {
	started := time.Now()
	cfg := q.llmConfig()
	status := "ERROR"
	message := "LLM недоступен"
	metrics := M{}
	base := strings.TrimRight(str(cfg, "base_url"), "/")
	path := "/api/tags"
	field := "models"
	id := "name"
	if cfg["provider"] == "openai" {
		if !strings.HasSuffix(base, "/v1") {
			base += "/v1"
		}
		path = "/models"
		field = "data"
		id = "id"
	}
	ctx, cancel := context.WithTimeout(q.Context, 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", base+path, nil)
	if err == nil {
		if key := str(cfg, "api_key"); key != "" {
			req.Header.Set("Authorization", "Bearer "+key)
		}
		response, err := (&http.Client{Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(req)
		if err == nil {
			defer response.Body.Close()
			var body M
			if response.StatusCode == 200 && json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&body) == nil {
				names := stringSet{}
				items, _ := body[field].([]any)
				for _, item := range items {
					if m, ok := item.(map[string]any); ok {
						names[str(m, id)] = true
					}
				}
				metrics["model_count"] = len(names)
				expected := str(cfg, "model")
				if names[expected] || (cfg["provider"] == "ollama" && !strings.Contains(expected, ":") && names[expected+":latest"]) {
					status = "OK"
					message = "API модели доступен"
				} else {
					status = "DEGRADED"
					message = "Выбранная модель отсутствует в списке доступных моделей"
				}
			}
		}
	}
	metrics["latency_ms"] = time.Since(started).Milliseconds()
	return M{"id": "ollama", "label": "LLM", "component_type": "llm", "status": status, "message": message, "metrics": metrics, "observed_at": time.Now(), "expires_at": nil}
}
