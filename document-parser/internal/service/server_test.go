package service

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/textproto"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/mumg/ai_secretary/document-parser/internal/parser"
)

var executable string

func TestMain(m *testing.M) {
	// The test binary can stand in for a parser stuck inside a document library.
	if len(os.Args) > 1 && os.Args[1] == "parse" && os.Getenv("IMPROVER_PARSER_TEST_SLEEP") == "1" {
		time.Sleep(time.Minute)
		os.Exit(0)
	}
	directory, err := os.MkdirTemp("", "improver-parser-test-")
	if err != nil {
		panic(err)
	}
	executable = filepath.Join(directory, "document-parser")
	if runtime.GOOS == "windows" {
		executable += ".exe"
	}
	command := exec.Command("go", "build", "-o", executable, "../../cmd/document-parser")
	command.Env = append(os.Environ(), "CGO_ENABLED=0")
	command.Stdout = os.Stdout
	command.Stderr = os.Stderr
	if err = command.Run(); err != nil {
		os.RemoveAll(directory)
		panic(err)
	}
	code := m.Run()
	os.RemoveAll(directory)
	os.Exit(code)
}
func upload(t *testing.T, handler http.Handler, filename string, data []byte) *httptest.ResponseRecorder {
	t.Helper()
	var body bytes.Buffer
	writer := multipart.NewWriter(&body)
	header := textproto.MIMEHeader{}
	header.Set("Content-Disposition", `form-data; name="file"; filename="`+filename+`"`)
	header.Set("Content-Type", "application/test-document")
	part, err := writer.CreatePart(header)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = part.Write(data); err != nil {
		t.Fatal(err)
	}
	writer.Close()
	request := httptest.NewRequest("POST", "/extract", &body)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}
func TestProcessRoundtrip(t *testing.T) {
	config := DefaultConfig()
	handler := Handler(config, ProcessExtractor(executable, config.MaxBytes))
	for _, name := range []string{"text.pdf", "text.docx", "values.xlsx", "shared-formulas.xlsx"} {
		t.Run(name, func(t *testing.T) {
			data, err := os.ReadFile("../../testdata/" + name)
			if err != nil {
				t.Fatal(err)
			}
			response := upload(t, handler, strings.ToUpper(name), data)
			if response.Code != 200 {
				t.Fatalf("%d %s", response.Code, response.Body.String())
			}
			var result parser.Result
			if err = json.Unmarshal(response.Body.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if result.Text == "" || result.MediaType != "application/test-document" || result.Truncated {
				t.Fatalf("unexpected result: %+v", result)
			}
		})
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "/health", nil))
	if response.Code != 200 || strings.TrimSpace(response.Body.String()) != `{"status":"ok"}` {
		t.Fatal(response.Body.String())
	}
	schemaResponse := httptest.NewRecorder()
	handler.ServeHTTP(schemaResponse, httptest.NewRequest("GET", "/openapi.json", nil))
	var schema struct {
		Paths map[string]any `json:"paths"`
	}
	if err := json.Unmarshal(schemaResponse.Body.Bytes(), &schema); err != nil || schema.Paths["/extract"] == nil {
		t.Fatalf("invalid OpenAPI: %s", schemaResponse.Body.String())
	}
	for _, name := range []string{"encrypted.pdf", "encrypted-empty.pdf"} {
		data, _ := os.ReadFile("../../testdata/" + name)
		response := upload(t, handler, name, data)
		if response.Code != 422 {
			t.Fatalf("encrypted %s: %d", name, response.Code)
		}
	}
}
func TestUploadErrorsAndTruncation(t *testing.T) {
	config := DefaultConfig()
	config.MaxBytes = 8
	handler := Handler(config, parser.Extract)
	for _, test := range []struct {
		name   string
		data   []byte
		status int
	}{{"file.txt", nil, 415}, {"file", nil, 415}, {"file.pdf", []byte("123456789"), 413}, {"file.docx", []byte("broken"), 422}, {"file.xlsx", []byte("broken"), 422}, {"file.pdf", []byte("broken"), 422}} {
		response := upload(t, handler, test.name, test.data)
		if response.Code != test.status || !strings.Contains(response.Body.String(), `"detail"`) {
			t.Errorf("%s: %d %s", test.name, response.Code, response.Body.String())
		}
	}
	var empty bytes.Buffer
	writer := multipart.NewWriter(&empty)
	writer.Close()
	request := httptest.NewRequest("POST", "/extract", &empty)
	request.Header.Set("Content-Type", writer.FormDataContentType())
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 422 {
		t.Fatal(response.Code)
	}
	config.MaxBytes = parser.DefaultMaxBytes
	config.MaxCharacters = 1
	data, _ := os.ReadFile("../../testdata/text.docx")
	response = upload(t, Handler(config, ProcessExtractor(executable, config.MaxBytes)), "text.docx", data)
	var result parser.Result
	json.Unmarshal(response.Body.Bytes(), &result)
	if response.Code != 200 || result.Text != "П" || !result.Truncated {
		t.Fatalf("%d %+v", response.Code, result)
	}
}
func TestTimeoutAndHealthWhileBusy(t *testing.T) {
	config := DefaultConfig()
	config.Timeout = 100 * time.Millisecond
	config.Concurrency = 1
	started := make(chan struct{})
	handler := Handler(config, func(ctx context.Context, _ []byte, _, _ string, _ int) (parser.Result, error) {
		close(started)
		<-ctx.Done()
		return parser.Result{}, ctx.Err()
	})
	server := httptest.NewServer(handler)
	defer server.Close()
	done := make(chan *httptest.ResponseRecorder, 1)
	go func() { done <- upload(t, handler, "file.pdf", []byte("data")) }()
	<-started
	response, err := http.Get(server.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, response.Body)
	response.Body.Close()
	if response.StatusCode != 200 {
		t.Fatal(response.StatusCode)
	}
	if result := <-done; result.Code != 504 {
		t.Fatal(result.Code)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := ProcessExtractor(executable, config.MaxBytes)(ctx, nil, ".pdf", "", 100); err != context.Canceled {
		t.Fatalf("ignored process cancellation: %v", err)
	}
}
func TestConfigurationValidation(t *testing.T) {
	for _, name := range []string{"MAX_BYTES", "MAX_CHARACTERS", "PARSER_CONCURRENCY", "PARSER_TIMEOUT"} {
		t.Run(name, func(t *testing.T) {
			t.Setenv(name, "-1")
			if _, err := ConfigFromEnv(); err == nil {
				t.Fatal("accepted invalid " + name)
			}
		})
	}
}

func TestDeadlineTerminatesChildProcess(t *testing.T) {
	t.Setenv("IMPROVER_PARSER_TEST_SLEEP", "1")
	helper, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, err = ProcessExtractor(helper, 100)(ctx, []byte("data"), ".pdf", "", 100)
	if err != context.DeadlineExceeded {
		t.Fatalf("expected deadline, got %v", err)
	}
	if time.Since(start) > 5*time.Second {
		t.Fatal("child process was not terminated promptly")
	}
}
