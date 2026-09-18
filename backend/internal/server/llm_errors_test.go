package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
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

func TestOllamaAnswerBudgetExcludesThinking(t *testing.T) {
	for _, mode := range []string{"matching", "chat", "stream"} {
		t.Run(mode, func(t *testing.T) {
			s := testServer(t)
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var payload M
				check(json.NewDecoder(r.Body).Decode(&payload))
				if payload["think"] != false {
					writeJSON(w, 200, M{"done_reason": "length", "message": M{"content": "", "thinking": "private reasoning"}})
					return
				}
				content := "Ready"
				if mode == "matching" {
					content = `{"matches":true,"confidence":0.9,"evidence":"Same topic"}`
				}
				if mode == "stream" {
					json.NewEncoder(w).Encode(M{"message": M{"content": content}, "done": false})
					json.NewEncoder(w).Encode(M{"message": M{"content": ""}, "done": true, "done_reason": "stop"})
				} else {
					writeJSON(w, 200, M{"message": M{"content": content}, "done_reason": "stop"})
				}
			}))
			defer upstream.Close()
			call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "ollama", "base_url": upstream.URL}}}, 200)
			_, err := s.job(context.Background(), func(q *request) bool {
				schema := ""
				if mode == "matching" {
					schema = "MeetingTopicMatch"
				}
				var emit func(string)
				emitted := ""
				if mode == "stream" {
					emit = func(s string) { emitted += s }
				}
				answer := must(q.llm("Test", M{}, schema, emit))
				if schema != "" {
					if !boolean(answer, "matches") {
						t.Error("match not returned")
					}
				} else if str(answer, "answer") != "Ready" {
					t.Error("chat answer not returned")
				}
				if mode == "stream" && emitted != "Ready" {
					t.Error("stream answer not emitted")
				}
				return true
			})
			if err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestLLMRejectsTruncatedAnswers(t *testing.T) {
	for _, provider := range []string{"ollama", "openai"} {
		for _, stream := range []bool{false, true} {
			for _, content := range []string{"", `{"reference_ids":[]}`} {
				t.Run(fmt.Sprintf("%s/stream=%v/content=%v", provider, stream, content != ""), func(t *testing.T) {
					s := testServer(t)
					upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						if provider == "ollama" {
							writeJSON(w, 200, M{"message": M{"content": content, "thinking": "private reasoning"}, "done": true, "done_reason": "length"})
							return
						}
						messageKey := "message"
						if stream {
							messageKey = "delta"
						}
						response := M{"choices": []M{{messageKey: M{"content": content}, "finish_reason": "length"}}}
						if stream {
							b, _ := json.Marshal(response)
							fmt.Fprintf(w, "data: %s\n\ndata: [DONE]\n\n", b)
						} else {
							writeJSON(w, 200, response)
						}
					}))
					defer upstream.Close()
					call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": provider, "base_url": upstream.URL}}}, 200)
					_, err := s.job(context.Background(), func(q *request) bool {
						var emit func(string)
						if stream {
							emit = func(string) {}
						}
						must(q.llm("Test", M{}, "RelevantReferenceSelection", emit))
						return true
					})
					var failure *llmFailure
					if !errors.As(err, &failure) || !strings.Contains(err.Error(), "лимиту токенов") {
						t.Fatalf("expected truncation diagnostic, got %v", err)
					}
				})
			}
		}
	}
}

func TestLLMTokenLimitRetriesDoubleBudget(t *testing.T) {
	for _, provider := range []string{"ollama", "openai"} {
		for _, succeed := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/success=%v", provider, succeed), func(t *testing.T) {
				s := testServer(t)
				var budgets []int
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					var payload M
					check(json.NewDecoder(r.Body).Decode(&payload))
					budget := int(num(obj(payload, "options"), "num_predict"))
					if provider == "openai" {
						budget = int(num(payload, "max_tokens"))
					}
					budgets = append(budgets, budget)
					reason, content := "length", ""
					if succeed && len(budgets) == 3 {
						reason, content = "stop", `{"reference_ids":["E1"]}`
					}
					if provider == "openai" {
						writeJSON(w, 200, M{"choices": []M{{"finish_reason": reason, "message": M{"content": content}}}})
					} else {
						writeJSON(w, 200, M{"done_reason": reason, "message": M{"content": content}})
					}
				}))
				defer upstream.Close()
				call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": provider, "base_url": upstream.URL}}}, 200)
				_, err := s.job(context.Background(), func(q *request) bool { must(q.llm("Select", M{}, "RelevantReferenceSelection", nil)); return true })
				if fmt.Sprint(budgets) != "[512 1024 2048]" {
					t.Fatal("incorrect retry budgets", budgets)
				}
				var failure *llmFailure
				if succeed {
					if err != nil {
						t.Fatal(err)
					}
				} else if !errors.As(err, &failure) || !failure.terminal || !strings.Contains(err.Error(), "Анализ не будет выполнен") {
					t.Fatal("missing permanent failure", err)
				}
			})
		}
	}
}

func TestLLMStreamRetryDiscardsPartialAttempt(t *testing.T) {
	s := testServer(t)
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		content, reason := "partial discarded", "length"
		if calls == 2 {
			content, reason = "complete answer", "stop"
		}
		json.NewEncoder(w).Encode(M{"message": M{"content": content}, "done": false})
		json.NewEncoder(w).Encode(M{"message": M{"content": ""}, "done": true, "done_reason": reason})
	}))
	defer upstream.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "ollama", "base_url": upstream.URL}}}, 200)
	var emitted string
	_, err := s.job(context.Background(), func(q *request) bool { must(q.llm("Test", M{}, "", func(s string) { emitted += s })); return true })
	if err != nil || calls != 2 || emitted != "complete answer" {
		t.Fatal("partial attempt leaked into answer", err, calls, emitted)
	}
}
