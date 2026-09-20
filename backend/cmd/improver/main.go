// improver is the native server executable. All database credentials are read
// from the same environment and secret files used by Docker and Windows.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/mumg/ai_secretary/backend/internal/config"
	"github.com/mumg/ai_secretary/backend/internal/server"
	"github.com/mumg/ai_secretary/backend/internal/store"
)

var version = "dev"

func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stderr, nil)))
	if e := run(); e != nil {
		slog.Error("server stopped", "error", e)
		os.Exit(1)
	}
}
func run() error {
	if version == "dev" {
		for _, path := range []string{"version", "../version"} {
			if b, e := os.ReadFile(path); e == nil {
				version = strings.TrimSpace(string(b))
				break
			}
		}
	}
	command := "serve"
	if len(os.Args) > 1 {
		command = os.Args[1]
	}
	if command == "version" {
		fmt.Println(version)
		return nil
	}
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	listen := flags.String("listen", "", "HTTP listen address")
	web := flags.String("web-dir", "", "Web asset directory")
	healthURL := flags.String("url", "http://127.0.0.1:8000/health/live", "Health endpoint")
	expectedVersion := flags.String("expected-version", "", "Require the running release version")
	ready := flags.Bool("ready", false, "Also require database readiness")
	if len(os.Args) > 2 {
		if e := flags.Parse(os.Args[2:]); e != nil {
			return e
		}
	}
	if command == "healthcheck" {
		client := http.Client{Timeout: 5 * time.Second}
		probe := func(endpoint string) (map[string]any, error) {
			r, e := client.Get(endpoint)
			if e != nil {
				return nil, errors.New("health endpoint unavailable")
			}
			defer r.Body.Close()
			if r.StatusCode != 200 {
				return nil, fmt.Errorf("health status %d", r.StatusCode)
			}
			var result map[string]any
			if e = json.NewDecoder(r.Body).Decode(&result); e != nil {
				return nil, errors.New("invalid health response")
			}
			return result, nil
		}
		live, e := probe(*healthURL)
		if e != nil {
			return e
		}
		if *expectedVersion != "" && live["version"] != *expectedVersion {
			return errors.New("running release version does not match")
		}
		if *ready {
			readiness, e := probe(strings.TrimSuffix(*healthURL, "/health/live") + "/health/ready")
			if e != nil {
				return e
			}
			if readiness["status"] != "ready" {
				return errors.New("database is not ready")
			}
		}
		return nil
	}
	if command != "serve" && command != "migrate" && command != "worker" {
		return fmt.Errorf("unknown command %q (serve, worker, migrate, healthcheck, version)", command)
	}
	cfg, e := config.Load()
	if e != nil {
		return e
	}
	if *listen != "" {
		cfg.Listen = *listen
	}
	if *web != "" {
		cfg.WebDir = *web
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	pool, e := pgxpool.New(ctx, cfg.DatabaseURL)
	if e != nil {
		return errors.New("invalid database configuration")
	}
	defer pool.Close()
	if command == "migrate" {
		return store.Migrate(ctx, pool)
	}
	for {
		var schema int
		e = pool.QueryRow(ctx, "SELECT version FROM database_schema_version WHERE id=1").Scan(&schema)
		if e == nil && schema >= 23 {
			break
		}
		slog.Info("waiting for database schema", "minimum_version", 23)
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(2 * time.Second):
		}
	}
	app := server.New(pool, cfg, version)
	if command == "worker" {
		app.Worker(ctx)
		return nil
	}
	app.Start(ctx)
	httpServer := &http.Server{Addr: cfg.Listen, Handler: app, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 90 * time.Second, MaxHeaderBytes: 1 << 20}
	stopped := make(chan error, 1)
	go func() { stopped <- httpServer.ListenAndServe() }()
	slog.Info("HTTP server started", "listen", cfg.Listen, "version", version)
	select {
	case e = <-stopped:
		if errors.Is(e, http.ErrServerClosed) {
			return nil
		}
		return e
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		return httpServer.Shutdown(shutdownCtx)
	}
}
