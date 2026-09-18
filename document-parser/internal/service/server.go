// Package service implements the document parser HTTP contract.
package service

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/document-parser/internal/parser"
)

//go:embed openapi.json
var openAPISchema []byte

type ExtractFunc func(context.Context, []byte, string, string, int) (parser.Result, error)
type Config struct {
	MaxBytes      int64
	MaxCharacters int
	Timeout       time.Duration
	Concurrency   int
}

func DefaultConfig() Config {
	return Config{MaxBytes: parser.DefaultMaxBytes, MaxCharacters: parser.DefaultMaxCharacters, Timeout: 30 * time.Second, Concurrency: 2}
}
func ConfigFromEnv() (Config, error) {
	config := DefaultConfig()
	for _, setting := range []struct {
		name    string
		value   *int64
		maximum int64
	}{{"MAX_BYTES", &config.MaxBytes, 256 * 1024 * 1024}} {
		if raw := os.Getenv(setting.name); raw != "" {
			value, err := strconv.ParseInt(raw, 10, 64)
			if err != nil || value < 1 || value > setting.maximum {
				return config, fmt.Errorf("invalid %s", setting.name)
			}
			*setting.value = value
		}
	}
	for _, setting := range []struct {
		name    string
		value   *int
		maximum int
	}{{"MAX_CHARACTERS", &config.MaxCharacters, 10000000}, {"PARSER_CONCURRENCY", &config.Concurrency, 16}} {
		if raw := os.Getenv(setting.name); raw != "" {
			value, err := strconv.Atoi(raw)
			if err != nil || value < 1 || value > setting.maximum {
				return config, fmt.Errorf("invalid %s", setting.name)
			}
			*setting.value = value
		}
	}
	if raw := os.Getenv("PARSER_TIMEOUT"); raw != "" {
		value, err := time.ParseDuration(raw)
		if err != nil || value < time.Millisecond || value > 10*time.Minute {
			return config, errors.New("invalid PARSER_TIMEOUT")
		}
		config.Timeout = value
	}
	return config, nil
}
func Handler(config Config, extract ExtractFunc) http.Handler {
	slots := make(chan struct{}, config.Concurrency)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /openapi.json", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(openAPISchema)
	})
	mux.HandleFunc("GET /health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("POST /extract", func(w http.ResponseWriter, r *http.Request) {
		ctx, cancel := context.WithTimeout(r.Context(), config.Timeout)
		defer cancel()
		select {
		case slots <- struct{}{}:
			defer func() { <-slots }()
		case <-ctx.Done():
			failure(w, http.StatusServiceUnavailable, "Parser is busy")
			return
		}
		deadline, _ := ctx.Deadline()
		_ = http.NewResponseController(w).SetReadDeadline(deadline)
		r.Body = http.MaxBytesReader(w, r.Body, config.MaxBytes+1024*1024)
		defer r.Body.Close()
		multipart, err := r.MultipartReader()
		if err != nil {
			failure(w, 422, "Expected multipart file upload")
			return
		}
		for {
			part, err := multipart.NextPart()
			if err == io.EOF {
				failure(w, 422, "Field required: file")
				return
			}
			if err != nil {
				uploadFailure(w, err)
				return
			}
			if part.FormName() != "file" || part.FileName() == "" {
				_, err = io.Copy(io.Discard, part)
				part.Close()
				if err != nil {
					uploadFailure(w, err)
					return
				}
				continue
			}
			suffix := strings.ToLower(filepath.Ext(part.FileName()))
			if suffix != ".pdf" && suffix != ".docx" && suffix != ".xlsx" {
				if suffix == "" {
					suffix = "unknown"
				}
				failure(w, 415, "Unsupported file type: "+suffix)
				return
			}
			data, err := io.ReadAll(io.LimitReader(part, config.MaxBytes+1))
			if int64(len(data)) > config.MaxBytes {
				failure(w, 413, "File is too large")
				return
			}
			if err != nil {
				uploadFailure(w, err)
				return
			}
			result, err := extract(ctx, data, suffix, part.Header.Get("Content-Type"), config.MaxCharacters)
			if ctx.Err() != nil {
				failure(w, 504, "Document parsing timed out")
				return
			}
			if err != nil {
				failure(w, 422, "Unable to parse document: "+err.Error())
				return
			}
			writeJSON(w, 200, result)
			return
		}
	})
	return mux
}
func uploadFailure(w http.ResponseWriter, err error) {
	var large *http.MaxBytesError
	if errors.As(err, &large) {
		failure(w, 413, "File is too large")
	} else {
		failure(w, 422, "Invalid multipart upload")
	}
}
func failure(w http.ResponseWriter, status int, detail string) {
	writeJSON(w, status, map[string]string{"detail": detail})
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

type cappedBuffer struct {
	bytes.Buffer
	limit int
}

func (b *cappedBuffer) Write(data []byte) (int, error) {
	if len(data) > b.limit-b.Len() {
		return 0, errors.New("parser output limit exceeded")
	}
	return b.Buffer.Write(data)
}

// ProcessExtractor keeps malformed document work outside the long-lived HTTP process.
// CommandContext terminates the child on a timeout or client cancellation on Linux and Windows.
func ProcessExtractor(executable string, maxBytes int64) ExtractFunc {
	return func(ctx context.Context, data []byte, suffix, mediaType string, limit int) (parser.Result, error) {
		command := exec.CommandContext(ctx, executable, "parse", "--suffix", suffix, "--media-type", mediaType, "--max-characters", strconv.Itoa(limit), "--max-bytes", strconv.FormatInt(maxBytes, 10))
		command.Env = append(os.Environ(), "GOMEMLIMIT=192MiB")
		command.Stdin = bytes.NewReader(data)
		output := &cappedBuffer{limit: limit*6 + 65536}
		diagnostic := &cappedBuffer{limit: 4096}
		command.Stdout = output
		command.Stderr = diagnostic
		if err := command.Run(); err != nil {
			if ctx.Err() != nil {
				return parser.Result{}, ctx.Err()
			}
			return parser.Result{}, errors.New("invalid, encrypted, or unsupported document")
		}
		var result parser.Result
		if err := json.Unmarshal(output.Bytes(), &result); err != nil {
			return result, errors.New("invalid parser response")
		}
		return result, nil
	}
}
