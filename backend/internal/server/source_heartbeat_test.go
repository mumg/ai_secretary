package server

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestSourceHeartbeatLifecycle(t *testing.T) {
	for _, end := range []string{"stop", "cancel", "delete"} {
		t.Run(end, func(t *testing.T) {
			s := testServer(t)
			call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": true, "settings": M{"host": "mail.example.test", "port": 993, "username": "demo@example.test"}, "credential": "test-password"}, 201)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			// Imports retain a foreign-key KEY SHARE lock until all events commit.
			importTx, err := s.Pool.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer importTx.Rollback(context.Background())
			if _, err = importTx.Exec(ctx, "SELECT id FROM communication_sources WHERE id='mail' FOR KEY SHARE"); err != nil {
				t.Fatal(err)
			}
			stop := s.sourceHeartbeat(ctx, "mail", 20*time.Millisecond)
			defer stop()
			read := func() (time.Time, string) {
				t.Helper()
				var observed, expires time.Time
				var status string
				if err := s.Pool.QueryRow(context.Background(), "SELECT observed_at,expires_at,status FROM component_statuses WHERE id='source-mail'").Scan(&observed, &expires, &status); err != nil {
					t.Fatal(err)
				}
				if !expires.After(observed) {
					t.Fatal("heartbeat has no expiration")
				}
				return observed, status
			}
			first, status := read()
			if status != "BUSY" {
				t.Fatal(status)
			}
			deadline := time.Now().Add(3 * time.Second)
			for {
				next, _ := read()
				if next.After(first) {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("heartbeat did not refresh")
				}
				time.Sleep(10 * time.Millisecond)
			}
			if err := importTx.Rollback(ctx); err != nil {
				t.Fatal(err)
			}
			switch end {
			case "stop":
				stop()
			case "cancel":
				cancel()
				stop()
			case "delete":
				call(t, s, "DELETE", "/api/v1/admin/sources/mail", nil, 204)
				time.Sleep(100 * time.Millisecond)
				stop()
				var n int
				if err := s.Pool.QueryRow(context.Background(), "SELECT count(*) FROM component_statuses WHERE id='source-mail'").Scan(&n); err != nil {
					t.Fatal(err)
				}
				if n != 0 {
					t.Fatal("heartbeat restored deleted source")
				}
				return
			}
			observed, _ := read()
			time.Sleep(80 * time.Millisecond)
			next, _ := read()
			if !next.Equal(observed) {
				t.Fatal("heartbeat continued after stopping")
			}
		})
	}
}

func TestSourceHeartbeatVisibleDuringImport(t *testing.T) {
	for _, code := range []int{http.StatusOK, http.StatusServiceUnavailable} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			s := testServer(t)
			entered, release := make(chan struct{}), make(chan struct{})
			var once sync.Once
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				once.Do(func() { close(entered) })
				<-release
				writeJSON(w, code, []any{})
			}))
			defer upstream.Close()
			var releaseOnce sync.Once
			unblock := func() { releaseOnce.Do(func() { close(release) }) }
			defer unblock()
			call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mts", "label": "MTS", "source_type": "mts_link", "settings": M{"base_url": upstream.URL}, "credential": "test-token"}, 201)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			done := make(chan struct{})
			go func() { defer close(done); s.syncSources(ctx) }()
			select {
			case <-entered:
			case <-ctx.Done():
				t.Fatal("import did not start")
			}
			var status string
			if err := s.Pool.QueryRow(ctx, "SELECT status FROM component_statuses WHERE id='source-mts'").Scan(&status); err != nil {
				t.Fatal(err)
			}
			if status != "BUSY" {
				t.Fatalf("uncommitted import status: %s", status)
			}
			unblock()
			select {
			case <-done:
			case <-ctx.Done():
				t.Fatal("import did not finish")
			}
			if err := s.Pool.QueryRow(ctx, "SELECT status FROM component_statuses WHERE id='source-mts'").Scan(&status); err != nil {
				t.Fatal(err)
			}
			want := "OK"
			if code != http.StatusOK {
				want = "ERROR"
			}
			if status != want {
				t.Fatalf("final status %s, want %s", status, want)
			}
		})
	}
}

func TestSourceHeartbeatCancellationDrainsActiveWrite(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": true, "settings": M{"host": "mail.example.test", "port": 993, "username": "demo@example.test"}, "credential": "test-password"}, 201)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stop := s.sourceHeartbeat(ctx, "mail", 20*time.Millisecond)
	defer stop()
	blocker, err := s.Pool.Begin(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	if _, err = blocker.Exec(context.Background(), "SELECT id FROM communication_sources WHERE id='mail' FOR UPDATE"); err != nil {
		t.Fatal(err)
	}
	// The blocker owns one connection; the next heartbeat needs a second one.
	deadline := time.Now().Add(3 * time.Second)
	for s.Pool.Stat().AcquiredConns() < 2 {
		if time.Now().After(deadline) {
			t.Fatal("heartbeat did not start")
		}
		time.Sleep(time.Millisecond)
	}
	cancel()
	stopped := make(chan struct{})
	go func() { stop(); close(stopped) }()
	select {
	case <-stopped:
		t.Fatal("stop returned before the active database operation finished")
	case <-time.After(50 * time.Millisecond):
	}
	if err = blocker.Rollback(context.Background()); err != nil {
		t.Fatal(err)
	}
	select {
	case <-stopped:
	case <-time.After(6 * time.Second):
		t.Fatal("heartbeat did not stop")
	}
	var before, after time.Time
	if err = s.Pool.QueryRow(context.Background(), "SELECT observed_at FROM component_statuses WHERE id='source-mail'").Scan(&before); err != nil {
		t.Fatal(err)
	}
	time.Sleep(80 * time.Millisecond)
	if err = s.Pool.QueryRow(context.Background(), "SELECT observed_at FROM component_statuses WHERE id='source-mail'").Scan(&after); err != nil {
		t.Fatal(err)
	}
	if !before.Equal(after) {
		t.Fatal("write completed after stop")
	}
}
