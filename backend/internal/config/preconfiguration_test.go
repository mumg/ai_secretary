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
	if len(c.Preconfiguration.Sources) != 2 || len(c.Preconfiguration.SetupWizard.Steps) != 4 {
		t.Fatal("expected sample wizard and sources")
	}
}

func TestPreconfigurationWizardValidation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "secretary-config.json")
	c := Config{PublicURL: "https://localhost", LLMURL: "http://localhost:11434"}
	base := `{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"type":"source","source_id":"mail","auth":"password","instructions":"Enter the mail password"},{"type":"llm","instructions":"Enter the model token"}]}}`
	if err := os.WriteFile(path, []byte(base), 0600); err != nil {
		t.Fatal(err)
	}
	if err := c.LoadPreconfiguration(path, true); err != nil {
		t.Fatal(err)
	}
	grouped := `{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"},{"id":"meetings","label":"Meetings","source_type":"mts_link"}],"setup_wizard":{"steps":[{"widgets":["source:mail","source:meetings"],"instructions":"Connect both"},{"widgets":["identity"],"instructions":"Enter people"},{"widgets":["llm"],"instructions":"Enter the model token"}]}}`
	if err := os.WriteFile(path, []byte(grouped), 0600); err != nil {
		t.Fatal(err)
	}
	if err := c.LoadPreconfiguration(path, true); err != nil {
		t.Fatal("multi-widget step", err)
	}
	for _, bad := range []string{
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"type":"source","source_id":"other","instructions":"Password"},{"type":"llm","instructions":"Token"}]}}`,
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"type":"source","source_id":"mail","auth":"sso","instructions":"Password"},{"type":"llm","instructions":"Token"}]}}`,
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"type":"source","source_id":"mail","instructions":"Password"}]}}`,
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"type":"source","source_id":"mail","instructions":"Password"},{"type":"llm","instructions":"Token","help_url":"javascript:alert(1)"}]}}`,
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"widgets":["source:mail","source:mail"],"instructions":"Password"},{"widgets":["llm"],"instructions":"Token"}]}}`,
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"widgets":["source:missing"],"instructions":"Password"},{"widgets":["llm"],"instructions":"Token"}]}}`,
		`{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap"}],"setup_wizard":{"steps":[{"widgets":["source:mail"],"instructions":"Password"},{"widgets":["identity","identity"],"instructions":"People"},{"widgets":["llm"],"instructions":"Token"}]}}`,
	} {
		if err := os.WriteFile(path, []byte(bad), 0600); err != nil {
			t.Fatal(err)
		}
		if err := c.LoadPreconfiguration(path, true); err == nil {
			t.Fatalf("accepted invalid wizard: %s", bad)
		}
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

func TestPreconfigurationInitialAssignmentDays(t *testing.T) {
	file := filepath.Join(t.TempDir(), "config.json")
	c := Config{PublicURL: "https://localhost", LLMURL: "http://localhost:11434"}
	for _, value := range []string{"0", "7", "365", "-1", "366", "1.5", `"7"`, "null"} {
		data := `{"schema_version":1,"sources":[{"id":"mail","label":"Mail","source_type":"imap","settings":{"initial_assignment_days":` + value + `}}]}`
		if err := os.WriteFile(file, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		err := c.LoadPreconfiguration(file, true)
		valid := value == "0" || value == "7" || value == "365"
		if (err == nil) != valid {
			t.Fatal(value, err)
		}
	}
}
