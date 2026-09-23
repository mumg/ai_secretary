package config

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"strings"
	"time"
	_ "time/tzdata"
)

type Object map[string]any
type Config struct {
	Preconfiguration Preconfiguration
	ClientCAFile     string
	ClientCAKeyFile  string
	DatabaseURL      string
	MasterKeyFile    string
	DataDir          string
	LocalOnly        bool
	PublicURL        string
	ParserURL        string
	LLMURL           string
	Listen           string
	WebDir           string
}

func Env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func Secret(path string) (string, error) {
	b, e := os.ReadFile(path)
	if e != nil {
		return "", fmt.Errorf("cannot read secret file %s", path)
	}
	s := strings.TrimSpace(string(b))
	if s == "" {
		return "", errors.New("empty secret file")
	}
	return s, nil
}
func Load() (Config, error) {
	c := Config{ClientCAFile: Env("CLIENT_ISSUER_CERT_FILE", "/run/client-issuer/client-ca.crt"), ClientCAKeyFile: Env("CLIENT_ISSUER_KEY_FILE", "/run/client-issuer/client-ca.key"), MasterKeyFile: Env("APP_MASTER_KEY_FILE", "/run/secrets/app_master_key"), DataDir: Env("DATA_DIR", "/data"), ParserURL: Env("DOCUMENT_PARSER_URL", "http://document-parser:8080"), LLMURL: Env("OLLAMA_BASE_URL", "http://ollama:11434"), Listen: Env("LISTEN_ADDR", "0.0.0.0:8000"), WebDir: Env("WEB_DIR", "web")}
	switch strings.ToLower(os.Getenv("LOCAL_WEB_ONLY")) {
	case "1", "true", "yes":
		c.LocalOnly = true
	}
	defaultURL := "https://localhost"
	if c.LocalOnly {
		defaultURL = "http://127.0.0.1:8000"
		if os.Getenv("LISTEN_ADDR") == "" {
			c.Listen = "127.0.0.1:8000"
		}
	}
	c.PublicURL = Env("PUBLIC_URL", defaultURL)
	if e := PublicURL(c.PublicURL); e != nil {
		return c, e
	}
	u, _ := url.Parse(c.PublicURL)
	if c.LocalOnly && u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1" {
		return c, errors.New("LOCAL_WEB_ONLY requires a loopback PUBLIC_URL")
	}
	raw := strings.Replace(Env("DATABASE_URL", "postgresql+asyncpg://improver@db:5432/improver"), "postgresql+asyncpg://", "postgresql://", 1)
	u, e := url.Parse(raw)
	if e != nil || u.User == nil || (u.Scheme != "postgresql" && u.Scheme != "postgres") {
		return c, errors.New("invalid PostgreSQL URL")
	}
	if _, ok := u.User.Password(); !ok {
		password, e := Secret(Env("DATABASE_PASSWORD_FILE", "/run/secrets/postgres_password"))
		if e != nil {
			return c, e
		}
		u.User = url.UserPassword(u.User.Username(), password)
	}
	c.DatabaseURL = u.String()
	e = c.loadAdjacentPreconfiguration()
	return c, e
}
func PublicURL(s string) error {
	u, e := url.Parse(s)
	if e != nil || u.Host == "" || u.User != nil {
		return errors.New("invalid public_url")
	}
	if u.Scheme != "https" && (u.Scheme != "http" || (u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1")) {
		return errors.New("public_url must use HTTPS, or HTTP on localhost/127.0.0.1")
	}
	return nil
}
func (c Config) Defaults() Object {
	return Merge(Object{
		"server":                Object{"timezone": "Europe/Moscow", "public_url": c.PublicURL, "log_level": Env("LOG_LEVEL", "INFO")},
		"calendar":              Object{"country": "RU", "workday_start": "10:00", "workday_end": "17:00", "daily_plan_time": "08:00", "auto_update": true, "weekend_due_policy": "previous_workday", "working_dates": []any{}, "non_working_dates": []any{}},
		"llm":                   Object{"provider": "ollama", "base_url": c.LLMURL, "model": "qwen3.8:27b-q4_K_M", "context_length": float64(16384), "temperature": 0.1, "auto_create_confidence": 0.85, "possible_completion_confidence": 0.8, "request_timeout_seconds": float64(300)},
		"worker":                Object{"poll_interval_seconds": float64(60), "batch_size": float64(10), "ranking_interval_seconds": float64(900)},
		"notifications":         Object{"due_soon_minutes": float64(60), "overdue_repeat_hour": float64(10), "mode": "immediate", "quiet_hours_enabled": false, "quiet_start": "22:00", "quiet_end": "08:00", "daily_summary": true},
		"document_parser":       Object{"timeout_seconds": float64(30), "max_bytes": float64(26214400), "max_characters": float64(100000)},
		"communication_sources": Object{"initial_sync_days": float64(30)}, "identity": Object{"names": []any{}}, "relationships": Object{"managers": []any{}, "reports": []any{}}, "analysis_filters": Object{"stop_words": []any{}, "excluded_addresses": []any{}},
	}, c.Preconfiguration.Settings)
}
func Section(o Object, k string) Object {
	switch v := o[k].(type) {
	case Object:
		return v
	case map[string]any:
		return Object(v)
	}
	return Object{}
}
func Text(o Object, k string) string { s, _ := o[k].(string); return s }
func Number(o Object, k string) float64 {
	switch v := o[k].(type) {
	case float64:
		return v
	case int:
		return float64(v)
	}
	return 0
}
func Merge(base, over Object) Object {
	for k, v := range over {
		if b, ok := base[k]; ok {
			switch b.(type) {
			case Object, map[string]any:
				for sk, sv := range Section(over, k) {
					Section(base, k)[sk] = sv
				}
				continue
			}
			base[k] = v
		}
	}
	return base
}
func Validate(p Object) error {
	server := Section(p, "server")
	if _, e := time.LoadLocation(Text(server, "timezone")); e != nil {
		return errors.New("invalid IANA timezone")
	}
	if e := PublicURL(Text(server, "public_url")); e != nil {
		return e
	}
	cal := Section(p, "calendar")
	for _, k := range []string{"workday_start", "workday_end", "daily_plan_time"} {
		if _, e := time.Parse("15:04", Text(cal, k)); e != nil {
			return fmt.Errorf("%s must use HH:MM", k)
		}
	}
	if Text(cal, "workday_start") >= Text(cal, "workday_end") {
		return errors.New("workday_start must precede workday_end")
	}
	for _, k := range []string{"working_dates", "non_working_dates"} {
		a, ok := cal[k].([]any)
		if !ok {
			return fmt.Errorf("%s must be an array", k)
		}
		for _, v := range a {
			s, ok := v.(string)
			if !ok {
				return errors.New("invalid calendar date")
			}
			if _, e := time.Parse("2006-01-02", s); e != nil {
				return e
			}
		}
	}
	llm := Section(p, "llm")
	if Text(llm, "provider") != "ollama" && Text(llm, "provider") != "openai" {
		return errors.New("invalid LLM provider")
	}
	u, e := url.Parse(Text(llm, "base_url"))
	if e != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return errors.New("invalid LLM base_url")
	}
	n := Section(p, "notifications")
	switch Text(n, "mode") {
	case "immediate", "digest", "important":
	default:
		return errors.New("invalid notification mode")
	}
	for _, key := range []string{"quiet_start", "quiet_end"} {
		if _, err := time.Parse("15:04", Text(n, key)); err != nil {
			return errors.New("invalid notification quiet time")
		}
	}
	for _, key := range []string{"quiet_hours_enabled", "daily_summary"} {
		if _, ok := n[key].(bool); !ok {
			return fmt.Errorf("notifications.%s must be boolean", key)
		}
	}
	if n["quiet_hours_enabled"] == true && Text(n, "quiet_start") == Text(n, "quiet_end") {
		return errors.New("quiet hours must have different start and end")
	}
	limits := []struct {
		s, k     string
		min, max float64
	}{{"llm", "context_length", 4096, 131072}, {"llm", "temperature", 0, 2}, {"llm", "request_timeout_seconds", 10, 1800}, {"llm", "auto_create_confidence", 0, 1}, {"llm", "possible_completion_confidence", 0, 1}, {"worker", "poll_interval_seconds", 10, 86400}, {"worker", "batch_size", 1, 100}, {"worker", "ranking_interval_seconds", 60, 86400}, {"notifications", "due_soon_minutes", 1, 10080}, {"notifications", "overdue_repeat_hour", 0, 23}, {"document_parser", "timeout_seconds", 5, 300}, {"document_parser", "max_bytes", 1048576, 26214400}, {"document_parser", "max_characters", 1000, 100000}, {"communication_sources", "initial_sync_days", 1, 365}}
	for _, l := range limits {
		v := Number(Section(p, l.s), l.k)
		if v < l.min || v > l.max {
			return fmt.Errorf("%s.%s out of range", l.s, l.k)
		}
	}
	return nil
}
func (c Config) aead() (cipher.AEAD, error) {
	secret, e := Secret(c.MasterKeyFile)
	if e != nil {
		return nil, e
	}
	if len([]rune(secret)) < 32 {
		return nil, errors.New("APP master key must contain at least 32 characters")
	}
	key := sha256.Sum256([]byte(secret))
	block, e := aes.NewCipher(key[:])
	if e != nil {
		return nil, e
	}
	return cipher.NewGCM(block)
}
func (c Config) Encrypt(s string) (string, error) {
	a, e := c.aead()
	if e != nil {
		return "", e
	}
	nonce := make([]byte, a.NonceSize())
	if _, e = rand.Read(nonce); e != nil {
		return "", e
	}
	data := a.Seal(nonce, nonce, []byte(s), []byte("improver:v1"))
	return base64.URLEncoding.EncodeToString(data), nil
}
func (c Config) Decrypt(s string) (string, error) {
	a, e := c.aead()
	if e != nil {
		return "", e
	}
	b, e := base64.URLEncoding.DecodeString(s)
	if e != nil || len(b) < a.NonceSize()+a.Overhead() {
		return "", errors.New("invalid encrypted secret")
	}
	plain, e := a.Open(nil, b[:a.NonceSize()], b[a.NonceSize():], []byte("improver:v1"))
	if e != nil {
		return "", errors.New("cannot decrypt secret")
	}
	return string(plain), nil
}
func Clone(o Object) Object {
	b, _ := json.Marshal(o)
	var r Object
	_ = json.Unmarshal(b, &r)
	return r
}

// ValidateInitialAssignmentDays validates the optional per-source history window.
func ValidateInitialAssignmentDays(settings Object) error {
	if value, exists := settings["initial_assignment_days"]; exists {
		days, ok := value.(float64)
		if !ok || days < 0 || days > 365 || days != float64(int(days)) {
			return errors.New("initial_assignment_days must be an integer from 0 to 365")
		}
	}
	return nil
}
