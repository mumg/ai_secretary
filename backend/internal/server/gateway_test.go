package server

import (
	"context"
	"errors"
	"github.com/mumg/ai_secretary/backend/internal/config"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestGatewayLocalSetupPersistsAndDoesNotExposeAdmin(t *testing.T) {
	dir := t.TempDir()
	s := New(nil, config.Config{DataDir: dir, LocalOnly: true}, "test")
	request := func(method, path, body string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(method, "http://localhost"+path, strings.NewReader(body))
		w := httptest.NewRecorder()
		s.ServeHTTP(w, r)
		return w
	}
	if w := request("GET", "/api/v1/admin/gateway", ""); w.Code != 200 || !strings.Contains(w.Body.String(), "not_configured") {
		t.Fatal(w.Body.String())
	}
	if s.gateway.settings.Address != defaultGatewayAddress {
		t.Fatal("missing default address")
	}
	id := s.gateway.settings.ID
	if len(id) != 36 {
		t.Fatal(id)
	}
	s2 := New(nil, config.Config{DataDir: dir, LocalOnly: true}, "test")
	if e := s2.gatewayLoad(); e != nil || s2.gateway.settings.ID != id {
		t.Fatal("UID not persisted", e)
	}
	for _, path := range []string{"/admin/", "/api/v1/admin/settings", "/api/v1/admin/gateway/qr", "/api/v1/admin/mobile-identity"} {
		w := httptest.NewRecorder()
		s.gatewayHandler().ServeHTTP(w, httptest.NewRequest("POST", "https://gateway.test"+path, strings.NewReader("{}")))
		if w.Code != 403 {
			t.Fatal("tunnel exposed", path, w.Code)
		}
	}
	w := request("POST", "/api/v1/admin/gateway/qr", "")
	if w.Code != 409 {
		t.Fatal(w.Code)
	}
	if s.gatewayDir() != filepath.Join(dir, "gateway") {
		t.Fatal(s.gatewayDir())
	}
}

func TestGatewayReregistrationKeepsOldIdentityUntilSuccess(t *testing.T) {
	s := New(nil, config.Config{DataDir: t.TempDir(), LocalOnly: true}, "test")
	if err := s.gatewayLoad(); err != nil {
		t.Fatal(err)
	}
	originalID := s.gateway.settings.ID
	fakeEnroll := func(ctx context.Context, dir, address, id, root string) error {
		if err := os.Mkdir(dir, 0700); err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(dir, "mobile-qr.png"), []byte(id), 0600)
	}
	if err := s.registerGateway(context.Background(), defaultGatewayAddress, false, fakeEnroll); err != nil {
		t.Fatal(err)
	}
	if s.gateway.settings.ID != originalID || !s.gatewayReady() {
		t.Fatal("initial UID was not used")
	}
	oldSettings, oldDir := s.gateway.settings, s.gatewayDir()
	s.gateway.connected = true
	now := time.Now()
	s.gateway.lastConnected = &now
	failEnroll := func(ctx context.Context, dir, address, id, root string) error {
		if id == originalID {
			t.Fatal("UID reused")
		}
		s.gateway.mu.Lock()
		defer s.gateway.mu.Unlock()
		if s.gateway.settings != oldSettings || !s.gateway.connected || !s.gatewayReady() {
			t.Fatal("old identity interrupted during issuance")
		}
		return errors.New("unavailable")
	}
	if err := s.registerGateway(context.Background(), "https://replacement.test", true, failEnroll); err == nil {
		t.Fatal("expected failure")
	}
	if s.gateway.settings != oldSettings || !s.gateway.connected || !s.gatewayReady() {
		t.Fatal("old identity lost on failure")
	}
	// Model the connector callback taking the state lock during cancellation.
	ctx, cancel := context.WithCancel(context.Background())
	s.gateway.cancel = cancel
	s.gateway.done = make(chan struct{})
	go func() {
		<-ctx.Done()
		s.gateway.mu.Lock()
		s.gateway.connected = false
		s.gateway.mu.Unlock()
		close(s.gateway.done)
	}()
	if err := s.registerGateway(context.Background(), "https://replacement.test", true, fakeEnroll); err != nil {
		t.Fatal(err)
	}
	if s.gateway.settings.ID == originalID || !s.gateway.settings.Enabled || s.gateway.lastConnected != nil || s.gateway.connected {
		t.Fatal("replacement not activated")
	}
	if _, err := os.Stat(oldDir); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("obsolete keys retained", err)
	}
	s2 := New(nil, s.Config, "test")
	if err := s2.gatewayLoad(); err != nil || s2.gateway.settings != s.gateway.settings || !s2.gatewayReady() {
		t.Fatal("replacement not persisted", err)
	}
	if s2.gatewayDir() != s.gatewayDir() {
		t.Fatal("replacement QR directory not selected")
	}

}

func TestGatewayRetryNeverReusesAttemptedUID(t *testing.T) {
	for _, scenario := range []string{"network failure", "interrupted request", "legacy incomplete", "save failure"} {
		t.Run(scenario, func(t *testing.T) {
			s := New(nil, config.Config{DataDir: t.TempDir(), LocalOnly: true}, "test")
			if err := s.gatewayLoad(); err != nil {
				t.Fatal(err)
			}
			id := s.gateway.settings.ID
			attemptedDir := filepath.Join(s.Config.DataDir, "gateway-"+id)
			if scenario == "interrupted request" {
				if err := os.Mkdir(attemptedDir, 0700); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "legacy incomplete" {
				if err := os.Mkdir(s.gatewayDir(), 0700); err != nil {
					t.Fatal(err)
				}
			}
			if scenario == "save failure" {
				if err := os.Mkdir(filepath.Join(s.Config.DataDir, "gateway.json.tmp"), 0700); err != nil {
					t.Fatal(err)
				}
			}
			attemptedID := ""
			failEnroll := func(ctx context.Context, dir, address, uid, root string) error {
				attemptedID = uid
				if err := os.Mkdir(dir, 0700); err != nil {
					t.Fatal(err)
				}
				return errors.New("request may have reached gateway")
			}
			if err := s.registerGateway(context.Background(), defaultGatewayAddress, false, failEnroll); err == nil {
				t.Fatal("expected error")
			}
			if scenario == "save failure" {
				if err := os.Remove(filepath.Join(s.Config.DataDir, "gateway.json.tmp")); err != nil {
					t.Fatal(err)
				}
			}
			if (scenario == "interrupted request" || scenario == "legacy incomplete") && attemptedID == id {
				t.Fatal("consumed UID reused")
			}
			previousAttempt := attemptedID
			_ = s.registerGateway(context.Background(), defaultGatewayAddress, false, failEnroll)
			if attemptedID == previousAttempt {
				t.Fatal("retry reused attempted UID")
			}
		})
	}
}

func TestGatewayRegistrationNeedsOnlyAddress(t *testing.T) {
	s := New(nil, config.Config{DataDir: t.TempDir(), LocalOnly: true}, "test")
	for _, route := range []string{"enroll", "reregister"} {
		w := httptest.NewRecorder()
		s.ServeHTTP(w, httptest.NewRequest("POST", "http://localhost/api/v1/admin/gateway/"+route, strings.NewReader(`{"gateway":"https://127.0.0.1:0"}`)))
		if w.Code != 409 || !strings.Contains(w.Body.String(), "сертификаты") {
			t.Fatal("name or invitation required", w.Code, w.Body.String())
		}
	}
}
