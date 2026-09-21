package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPreconfigurationLoadAndSparseDifferences(t *testing.T) {
	dir := t.TempDir()
	file := filepath.Join(dir, "секретарь.json")
	c := Config{PublicURL: "https://localhost", LLMURL: "http://localhost:11434"}
	if err := c.LoadPreconfiguration(file, false); err != nil {
		t.Fatal(err)
	}
	if err := c.LoadPreconfiguration(file, true); err == nil {
		t.Fatal("missing explicit file accepted")
	}
	data := `{"schema_version":1,"settings":{"llm":{"model":"Корпоративная модель"},"calendar":{"auto_update":false}},"sources":[{"id":"mail","label":"Почта","source_type":"imap","settings":{"host":"imap.example.test","port":993,"tls":true}}]}`
	if err := os.WriteFile(file, []byte(data), 0600); err != nil {
		t.Fatal(err)
	}
	if err := c.LoadPreconfiguration(file, true); err != nil {
		t.Fatal(err)
	}
	if Text(Section(c.Defaults(), "llm"), "model") != "Корпоративная модель" {
		t.Fatal(c.Defaults())
	}
	effective := c.Defaults()
	Section(effective, "llm")["temperature"] = 0.4
	delta := Difference(c.Defaults(), effective)
	if len(delta) != 1 || Number(Section(delta, "llm"), "temperature") != 0.4 {
		t.Fatal(delta)
	}
	for _, bad := range []string{`null`, `{"schema_version":2}`, `{"schema_version":1,"unknown":1}`, `{"schema_version":1,"settings":{"llm":{"modle":"oops"}}}`, `{"schema_version":1,"settings":{"calendar":{"auto_update":"false"}}}`, `{"schema_version":1} {}`, `{"schema_version":1,"sources":[{"id":"a","label":"A","source_type":"imap","enabled":true}]}`} {
		if err := os.WriteFile(file, []byte(bad), 0600); err != nil {
			t.Fatal(err)
		}
		if err := c.LoadPreconfiguration(file, true); err == nil {
			t.Fatalf("accepted %s", bad)
		}
	}
}
func TestDifferencePreservesEmptyAndFalse(t *testing.T) {
	base := Object{"x": Object{"enabled": true, "names": []any{"A"}, "text": "value", "zero": float64(1)}}
	value := Object{"x": Object{"enabled": false, "names": []any{}, "text": "", "zero": float64(0)}}
	delta := Difference(base, value)
	if len(Section(delta, "x")) != 4 {
		t.Fatal(delta)
	}
	merged := Merge(Clone(base), delta)
	if len(Difference(value, merged)) != 0 {
		t.Fatal(merged)
	}
}

func TestShippedPreconfigurationExample(t *testing.T) {
	c := Config{PublicURL: "https://localhost", LLMURL: "http://localhost:11434"}
	if err := c.LoadPreconfiguration("../../../config/secretary-config.example.json", true); err != nil {
		t.Fatal(err)
	}
	if len(c.Preconfiguration.Sources) != 1 {
		t.Fatal("expected sample source")
	}
}

func TestLoadUsesExplicitPolicyFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "secretary-config.json")
	if err := os.WriteFile(path, []byte(`{"schema_version":1,"settings":{"llm":{"model":"loaded-from-file"}}}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
	t.Setenv("APP_CONFIG_FILE", path)
	c, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if Text(Section(c.Defaults(), "llm"), "model") != "loaded-from-file" {
		t.Fatal("baseline lost in Load")
	}
}
