package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/mumg/ai_secretary/document-parser/internal/parser"
	"github.com/mumg/ai_secretary/document-parser/internal/service"
)

var version = "dev"

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run(args []string) error {
	command := "serve"
	if len(args) > 0 {
		command = args[0]
		args = args[1:]
	}
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	switch command {
	case "version":
		fmt.Println(version)
		return nil
	case "healthcheck":
		url := flags.String("url", "http://127.0.0.1:8080/health", "health endpoint")
		if err := flags.Parse(args); err != nil {
			return err
		}
		client := http.Client{Timeout: 2 * time.Second}
		response, err := client.Get(*url)
		if err != nil {
			return err
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			return errors.New("parser is unhealthy")
		}
		return nil
	case "parse":
		suffix := flags.String("suffix", "", "document suffix")
		mediaType := flags.String("media-type", "application/octet-stream", "original MIME type")
		characters := flags.Int("max-characters", parser.DefaultMaxCharacters, "text limit")
		maxBytes := flags.Int64("max-bytes", parser.DefaultMaxBytes, "upload limit")
		if err := flags.Parse(args); err != nil {
			return err
		}
		if *maxBytes < 1 || *maxBytes > 256*1024*1024 {
			return errors.New("invalid byte limit")
		}
		data, err := io.ReadAll(io.LimitReader(os.Stdin, *maxBytes+1))
		if err != nil {
			return err
		}
		if int64(len(data)) > *maxBytes {
			return errors.New("file is too large")
		}
		result, err := parser.Extract(context.Background(), data, *suffix, *mediaType, *characters)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(result)
	case "serve":
		listen := flags.String("listen", ":8080", "listen address")
		if err := flags.Parse(args); err != nil {
			return err
		}
		config, err := service.ConfigFromEnv()
		if err != nil {
			return err
		}
		executable, err := os.Executable()
		if err != nil {
			return err
		}
		server := &http.Server{Addr: *listen, Handler: service.Handler(config, service.ProcessExtractor(executable, config.MaxBytes)), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: config.Timeout, WriteTimeout: config.Timeout + 5*time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 64 * 1024}
		ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		done := make(chan struct{})
		defer close(done)
		go func() {
			select {
			case <-ctx.Done():
				shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				_ = server.Shutdown(shutdown)
			case <-done:
			}
		}()
		log.Printf("document parser %s listening on %s", version, *listen)
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		return nil
	default:
		return fmt.Errorf("unknown command %q", command)
	}
}
