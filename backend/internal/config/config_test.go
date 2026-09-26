package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestPythonEncryptedSecretCompatibility(t *testing.T) {
	path := filepath.Join(t.TempDir(), "master")
	if err := os.WriteFile(path, []byte("compatibility-fixture-master-key-32\n"), 0600); err != nil {
		t.Fatal(err)
	}
	c := Config{MasterKeyFile: path}
	fixture := "AAECAwQFBgcICQoLV63yPmC2P-wcdDu3YZgU3wd_eSMWt15NccEhzqe3FlwF3EMG_Q=="
	plain, err := c.Decrypt(fixture)
	if err != nil || plain != "Пароль: +/&🙂" {
		t.Fatal(plain, err)
	}
	first, err := c.Encrypt(plain)
	if err != nil {
		t.Fatal(err)
	}
	second, err := c.Encrypt(plain)
	if err != nil || first == second {
		t.Fatal("nonce reused", err)
	}
	if _, err = c.Decrypt(first[:len(first)-4] + "AAAA"); err == nil {
		t.Fatal("tampered ciphertext accepted")
	}
}
func TestDatabasePasswordURLQuoting(t *testing.T) {
	path := filepath.Join(t.TempDir(), "password")
	os.WriteFile(path, []byte("a@:/? &+"), 0600)
	t.Setenv("DATABASE_URL", "postgresql+asyncpg://improver@localhost/db")
	t.Setenv("DATABASE_PASSWORD_FILE", path)
	t.Setenv("PUBLIC_URL", "http://127.0.0.1:8000")
	t.Setenv("LOCAL_WEB_ONLY", "true")
	t.Setenv("LISTEN_ADDR", "")
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if c.Listen != "127.0.0.1:8000" || c.DatabaseURL != "postgresql://improver:a%40%3A%2F%3F%20&+@localhost/db" {
		t.Fatal(c.DatabaseURL, c.Listen)
	}
}

func TestNotificationPolicyValidation(t *testing.T) {
	c := Config{PublicURL: "https://localhost", LLMURL: "http://localhost:11434"}
	for _, mode := range []string{"immediate", "digest", "important"} {
		p := c.Defaults()
		Section(p, "notifications")["mode"] = mode
		if err := Validate(p); err != nil {
			t.Fatal(err)
		}
	}
	for _, patch := range []Object{{"mode": "unknown"}, {"quiet_start": "25:00"}, {"quiet_hours_enabled": "true"}, {"daily_summary": 1}, {"quiet_hours_enabled": true, "quiet_start": "08:00", "quiet_end": "08:00"}} {
		p := c.Defaults()
		Merge(p, Object{"notifications": patch})
		if Validate(p) == nil {
			t.Fatal("invalid policy accepted", patch)
		}
	}
}

func TestLLMPromptCorrectionsValidation(t *testing.T) {
	c := Config{PublicURL: "https://localhost", LLMURL: "http://localhost:11434"}
	p := c.Defaults()
	Section(p, "llm_prompt_corrections")["task_extraction"] = "Не создавай задачу из информационного статуса."
	if err := Validate(p); err != nil {
		t.Fatal(err)
	}
	for _, invalid := range []any{42, strings.Repeat("a", 4001), "test\x00value"} {
		bad := c.Defaults()
		Section(bad, "llm_prompt_corrections")["task_extraction"] = invalid
		if Validate(bad) == nil {
			t.Fatal("invalid prompt correction accepted")
		}
	}
	bad := c.Defaults()
	Section(bad, "llm_prompt_corrections")["unknown"] = "text"
	if Validate(bad) == nil {
		t.Fatal("unknown prompt correction accepted")
	}
}
