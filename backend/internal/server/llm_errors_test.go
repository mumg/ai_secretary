package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestLLMTransmitsAuthoredSchemaOrder(t *testing.T) {
	for _, provider := range []string{"ollama", "openai"} {
		t.Run(provider, func(t *testing.T) {
			s := testServer(t)
			calls := 0
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				var payload map[string]json.RawMessage
				if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
					t.Error(err)
					w.WriteHeader(400)
					return
				}
				schema := payload["format"]
				if provider == "openai" {
					var format struct {
						JSONSchema struct {
							Schema json.RawMessage `json:"schema"`
						} `json:"json_schema"`
					}
					if err := json.Unmarshal(payload["response_format"], &format); err != nil {
						t.Error(err)
					}
					schema = format.JSONSchema.Schema
				}
				var want, got bytes.Buffer
				check(json.Compact(&want, llmWireSchemas["AnalysisResult"]))
				if err := json.Compact(&got, schema); err != nil || !bytes.Equal(want.Bytes(), got.Bytes()) {
					t.Error("generation schema property order changed on the wire")
				}
				content := `{"summary":"Message summary","thread_summary":"Thread summary"}`
				if provider == "openai" {
					writeJSON(w, 200, M{"choices": []M{{"finish_reason": "stop", "message": M{"content": content}}}})
				} else {
					writeJSON(w, 200, M{"message": M{"content": content}})
				}
			}))
			defer upstream.Close()
			call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": provider, "base_url": upstream.URL}}}, 200)
			_, err := s.job(context.Background(), func(q *request) bool {
				answer := must(q.llm("Analyze", M{"body": "Message"}, "AnalysisResult", nil))
				if str(answer, "summary") != "Message summary" {
					t.Error("unexpected analysis result")
				}
				return true
			})
			if err != nil || calls != 1 {
				t.Fatal("schema request failed", err, calls)
			}
		})
	}
}

func TestLLMFailuresRemainSafeAndSpecific(t *testing.T) {
	for _, tc := range []struct {
		name, expected string
		status         int
		response       M
	}{
		{"server", "HTTP 503", 503, M{"error": "private-provider-details"}},
		{"truncated", "лимиту токенов", 200, M{"choices": []M{{"finish_reason": "length", "message": M{"content": "private-incomplete-json"}}}}},
		{"timeout", "время ожидания", 0, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := testServer(t)
			release := make(chan struct{})
			model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if tc.status == 0 {
					select {
					case <-r.Context().Done():
					case <-release:
					}
					return
				}
				writeJSON(w, tc.status, tc.response)
			}))
			defer model.Close()
			defer close(release)
			call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "openai", "base_url": model.URL}}}, 200)
			ctx := context.Background()
			if tc.status == 0 {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, 100*time.Millisecond)
				defer cancel()
			}
			_, err := s.job(ctx, func(q *request) bool { must(q.llm("Test", M{}, "AnalysisResult", nil)); return true })
			var failure *llmFailure
			if !errors.As(err, &failure) || !strings.Contains(err.Error(), tc.expected) || strings.Contains(err.Error(), "private") {
				t.Fatalf("unexpected diagnostic: %v", err)
			}
		})
	}
}
