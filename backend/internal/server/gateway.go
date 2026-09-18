package server

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/gatewaylink"
)

type gatewaySettings struct {
	ID               string `json:"installation_id"`
	Address          string `json:"gateway"`
	IsolatedIdentity bool   `json:"isolated_identity,omitempty"`
	Enabled          bool   `json:"enabled"`
}
type gatewayState struct {
	opMu          sync.Mutex // Serializes mutations while waiting for the old tunnel to stop.
	mu            sync.Mutex
	settings      gatewaySettings
	loaded        bool
	ctx           context.Context
	cancel        context.CancelFunc
	done          chan struct{}
	connected     bool
	lastConnected *time.Time
}

const defaultGatewayAddress = "https://ai.muratov.net"

func (s *Server) gatewaySettingsDir(cfg gatewaySettings) string {
	if cfg.IsolatedIdentity {
		return filepath.Join(s.Config.DataDir, "gateway-"+cfg.ID)
	}
	return filepath.Join(s.Config.DataDir, "gateway")
}
func (s *Server) gatewayDir() string { return s.gatewaySettingsDir(s.gateway.settings) }
func (s *Server) gatewayLoad() error {
	if s.gateway.loaded {
		return nil
	}
	b, e := os.ReadFile(filepath.Join(s.Config.DataDir, "gateway.json"))
	if e != nil && !errors.Is(e, os.ErrNotExist) {
		return e
	}
	if e == nil {
		if e = json.Unmarshal(b, &s.gateway.settings); e != nil {
			return e
		}
	}
	if s.gateway.settings.ID == "" || s.gateway.settings.Address == "" {
		if s.gateway.settings.ID == "" {
			s.gateway.settings.ID = newID()
		}
		if s.gateway.settings.Address == "" {
			s.gateway.settings.Address = defaultGatewayAddress
		}
		if e = s.gatewaySave(); e != nil {
			return e
		}
	}
	s.gateway.loaded = true
	return nil
}
func (s *Server) gatewaySave() error {
	if e := os.MkdirAll(s.Config.DataDir, 0700); e != nil {
		return e
	}
	b, e := json.Marshal(s.gateway.settings)
	if e != nil {
		return e
	}
	path := filepath.Join(s.Config.DataDir, "gateway.json")
	if e = os.WriteFile(path+".tmp", b, 0600); e != nil {
		return e
	}
	return os.Rename(path+".tmp", path)
}
func (s *Server) gatewayReady() bool {
	_, e := os.Stat(filepath.Join(s.gatewayDir(), "mobile-qr.png"))
	return e == nil
}
func (s *Server) gatewayStatus() M {
	g := &s.gateway
	state := "not_configured"
	if _, e := os.Stat(s.gatewayDir()); e == nil {
		state = "enrollment_incomplete"
	}
	if s.gatewayReady() {
		state = "paused"
		if g.settings.Enabled {
			state = "reconnecting"
		}
		if g.connected {
			state = "connected"
		}
	}
	return M{"installation_id": g.settings.ID, "gateway": g.settings.Address, "enabled": g.settings.Enabled, "state": state, "qr_available": s.gatewayReady(), "last_connected_at": g.lastConnected}
}

// Only the mobile API is reachable from the tunnel. Settings and QR issuance
// remain available exclusively through the existing local/protected listener.
func (s *Server) gatewayHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/v1/") || r.URL.Path == "/api/v1/admin" || strings.HasPrefix(r.URL.Path, "/api/v1/admin/") || r.Header.Get("Origin") != "" {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		r.Host = "localhost"
		for _, h := range []string{"Forwarded", "X-Forwarded-Host", "X-Forwarded-For", "X-Forwarded-Proto"} {
			r.Header.Del(h)
		}
		s.ServeHTTP(w, r)
	})
}
func (s *Server) gatewayRunLocked() {
	g := &s.gateway
	if g.cancel != nil || g.ctx == nil || !g.settings.Enabled || !s.gatewayReady() {
		return
	}
	ctx, cancel := context.WithCancel(g.ctx)
	g.cancel = cancel
	g.done = make(chan struct{})
	done := g.done
	dir := s.gatewayDir()
	go func() {
		defer close(done)
		_ = gatewaylink.Run(ctx, dir, s.gatewayHandler(), func(connected bool) {
			g.mu.Lock()
			defer g.mu.Unlock()
			g.connected = connected
			if connected {
				now := time.Now()
				g.lastConnected = &now
			}
		})
	}()
}
func (s *Server) startGateway(ctx context.Context) {
	s.gateway.mu.Lock()
	defer s.gateway.mu.Unlock()
	s.gateway.ctx = ctx
	if s.gatewayLoad() == nil {
		s.gatewayRunLocked()
	}
}
func (s *Server) gatewayRoutes() {
	s.route("GET /api/v1/admin/gateway", false, func(q *request) any {
		s.gateway.mu.Lock()
		defer s.gateway.mu.Unlock()
		check(s.gatewayLoad())
		q.w.Header().Set("Cache-Control", "no-store")
		return s.gatewayStatus()
	})
	for _, operation := range []string{"enroll", "reregister"} {
		s.route("POST /api/v1/admin/gateway/"+operation, false, func(q *request) any {
			m := q.body()
			textField(m, "gateway", 1, 2048, true)
			address := strings.TrimRight(str(m, "gateway"), "/")
			u, e := url.Parse(address)
			if e != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || u.Path != "" || u.RawQuery != "" || u.Fragment != "" {
				fail(422, "Укажите HTTPS-адрес гейтвея без пути и параметров")
			}
			ctx, cancel := context.WithTimeout(q.Context, 40*time.Second)
			defer cancel()
			if e := s.registerGateway(ctx, address, operation == "reregister", gatewaylink.Enroll); e != nil {
				fail(409, e.Error())
			}
			s.gateway.mu.Lock()
			defer s.gateway.mu.Unlock()
			return s.gatewayStatus()
		})
	}
	s.route("PUT /api/v1/admin/gateway", false, func(q *request) any {
		m := q.body()
		enabled, ok := m["enabled"].(bool)
		if !ok {
			fail(422, "enabled must be a boolean")
		}
		s.gateway.opMu.Lock()
		defer s.gateway.opMu.Unlock()
		s.gateway.mu.Lock()
		defer s.gateway.mu.Unlock()
		check(s.gatewayLoad())
		if !s.gatewayReady() {
			fail(409, "Сначала зарегистрируйте сервер")
		}
		s.gateway.settings.Enabled = enabled
		check(s.gatewaySave())
		if !enabled {
			s.gatewayStopLocked()
		}
		s.gatewayRunLocked()
		return s.gatewayStatus()
	})
	s.route("POST /api/v1/admin/gateway/qr", false, func(q *request) any {
		s.gateway.mu.Lock()
		defer s.gateway.mu.Unlock()
		q.w.Header().Set("Cache-Control", "no-store")
		q.w.Header().Set("Pragma", "no-cache")
		check(s.gatewayLoad())
		if !s.gatewayReady() {
			fail(409, "Сначала зарегистрируйте сервер")
		}
		png := must(gatewaylink.QRImage(s.gatewayDir()))
		return M{"qr_image": "data:image/png;base64," + base64.StdEncoding.EncodeToString(png)}
	})
}

// Called with both mutation and state locks held; callbacks need the state lock.
func (s *Server) gatewayStopLocked() {
	g := &s.gateway
	if g.cancel != nil {
		g.cancel()
		done := g.done
		g.mu.Unlock()
		<-done
		g.mu.Lock()
		g.cancel = nil
	}
	g.connected = false
}

// Keep the active identity intact until the replacement certificates and QR are
// complete. gateway.json atomically selects the identity for API and workers.
func (s *Server) registerGateway(ctx context.Context, address string, replace bool, enroll func(context.Context, string, string, string, string) error) error {
	g := &s.gateway
	g.opMu.Lock()
	defer g.opMu.Unlock()
	g.mu.Lock()
	defer g.mu.Unlock()
	if e := s.gatewayLoad(); e != nil {
		return e
	}
	ready := s.gatewayReady()
	if ready && !replace {
		return errors.New("Сервер уже зарегистрирован. Используйте перерегистрацию")
	}
	previous := g.settings
	oldDir := s.gatewayDir()
	next := gatewaySettings{ID: previous.ID, Address: address, Enabled: true, IsolatedIdentity: true}
	_, oldDirError := os.Stat(oldDir)
	if replace || (!ready && !errors.Is(oldDirError, os.ErrNotExist)) {
		next.ID = newID()
	}
	dir := s.gatewaySettingsDir(next)
	// An interrupted request may have consumed its UID on the gateway.
	if _, e := os.Stat(dir); !errors.Is(e, os.ErrNotExist) {
		next.ID = newID()
		dir = s.gatewaySettingsDir(next)
	}
	g.mu.Unlock()
	err := enroll(ctx, dir, address, next.ID, "")
	g.mu.Lock()
	if err != nil {
		if !ready {
			g.settings.ID = newID()
			g.settings.Address = address
			if e := s.gatewaySave(); e != nil {
				g.settings = previous
				return e
			}
		}
		_ = os.RemoveAll(dir)
		message := "Не удалось получить сертификаты. Проверьте адрес и доступность гейтвея и повторите попытку."
		if ready {
			message += " Действующая регистрация сохранена."
		}
		return errors.New(message)
	}
	g.settings = next
	if err = s.gatewaySave(); err != nil {
		g.settings = previous
		// Retain the attempted directory: this UID has already been consumed.
		return err
	}
	s.gatewayStopLocked()
	g.lastConnected = nil
	s.gatewayRunLocked()
	if oldDir != dir {
		_ = os.RemoveAll(oldDir)
	}
	return nil
}

// Workers are separate processes; read the persisted enabled flag for each batch.
func (s *Server) gatewayNotify(ctx context.Context, kind, id string) int {
	b, e := os.ReadFile(filepath.Join(s.Config.DataDir, "gateway.json"))
	if e != nil {
		return 0
	}
	var cfg gatewaySettings
	if json.Unmarshal(b, &cfg) != nil || !cfg.Enabled {
		return 0
	}
	count, err := gatewaylink.Notify(ctx, s.gatewaySettingsDir(cfg), kind, id, newID)
	if err != nil {
		slog.Warn("gateway notification unavailable", "type", kind)
	}
	return count
}
