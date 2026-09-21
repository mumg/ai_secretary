package server

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

func TestPreconfigurationSettingsAndSources(t *testing.T) {
	s := testServer(t)
	s.Config.Preconfiguration = config.Preconfiguration{SchemaVersion: 1, Settings: config.Object{"llm": config.Object{"model": "policy-model"}, "identity": config.Object{"names": []any{"Owner"}}}, Sources: []config.SourceDefaults{{ID: "corporate", Label: "Corporate mail", SourceType: "imap", Settings: config.Object{"host": "imap.example.test", "port": float64(993), "tls": true}}}}
	ctx := context.Background()
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	q := &request{Context: ctx, server: s, db: s.Pool}
	settings := call(t, s, "GET", "/api/v1/admin/settings", nil, 200).(map[string]any)
	full := obj(settings, "settings")
	if str(obj(full, "llm"), "model") != "policy-model" {
		t.Fatal(settings)
	}
	obj(full, "llm")["temperature"] = 0.3
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": full}, 200)
	stored := q.one("SELECT payload FROM system_settings WHERE id=1")
	if hash(obj(stored, "payload")) != hash(M{"llm": M{"temperature": 0.3}}) {
		t.Fatalf("copied baseline: %v", stored)
	}
	rows := call(t, s, "GET", "/api/v1/admin/sources", nil, 200).([]any)
	if len(rows) != 1 {
		t.Fatal(rows)
	}
	source := rows[0].(map[string]any)
	if source["label"] != "Corporate mail" || source["enabled"] != false {
		t.Fatal(source)
	}
	source["enabled"] = true
	source["credential"] = "user-secret"
	obj(source, "settings")["username"] = "user@example.test"
	call(t, s, "PUT", "/api/v1/admin/sources/corporate", source, 200)
	var raw []byte
	if err := s.Pool.QueryRow(ctx, "SELECT row_to_json(s) FROM communication_sources s WHERE id='corporate'").Scan(&raw); err != nil {
		t.Fatal(err)
	}
	var persisted M
	if err := json.Unmarshal(raw, &persisted); err != nil {
		t.Fatal(err)
	}
	if persisted["label"] != nil || persisted["source_type"] != nil || strings.Contains(string(raw), "imap.example.test") || strings.Contains(string(raw), "user-secret") {
		t.Fatalf("baseline or plaintext secret copied: %s", raw)
	}
	if obj(persisted, "settings")["username"] != "user@example.test" {
		t.Fatal(persisted)
	}
	if len(q.rows("SELECT id FROM communication_sources WHERE enabled AND source_type='imap'")) != 1 {
		t.Fatal("worker cannot see configured source")
	}
	q.insert("source_cursors", M{"source_id": "corporate", "cursor_key": "INBOX", "cursor_value": "100"})
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	if len(q.rows("SELECT id FROM source_cursors WHERE source_id=$1", "corporate")) != 1 {
		t.Fatal("unchanged baseline reset cursor")
	}
	// Simulate restart with a replacement baseline. User differences survive.
	config.Section(s.Config.Preconfiguration.Settings, "llm")["model"] = "updated-model"
	s.Config.Preconfiguration.Sources[0].Settings["host"] = "new.example.test"
	s.Config.Preconfiguration.Sources[0].Label = "New corporate label"
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	if len(q.rows("SELECT id FROM source_cursors WHERE source_id=$1", "corporate")) != 0 {
		t.Fatal("new endpoint retained old cursor")
	}
	effective := q.get("communication_sources", "corporate")
	if effective["label"] != "New corporate label" || obj(effective, "settings")["host"] != "new.example.test" || obj(effective, "settings")["username"] != "user@example.test" {
		t.Fatal(effective)
	}
	if obj(q.settings(), "llm")["model"] != "updated-model" || num(obj(q.settings(), "llm"), "temperature") != 0.3 {
		t.Fatal(q.settings())
	}
	// Locking reads used by external import and OAuth must remain valid.
	if _, err := s.job(ctx, func(q *request) bool {
		q.one("SELECT * FROM communication_sources WHERE id=$1 FOR UPDATE", "corporate")
		return true
	}); err != nil {
		t.Fatal(err)
	}
	call(t, s, "DELETE", "/api/v1/admin/sources/corporate", nil, 204)
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	if rows := call(t, s, "GET", "/api/v1/admin/sources", nil, 200).([]any); len(rows) != 0 {
		t.Fatal("deleted source resurrected", rows)
	}
}
func TestPreconfigurationDoesNotReplaceExistingSource(t *testing.T) {
	s := testServer(t)
	ctx := context.Background()
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "existing", "label": "User label", "source_type": "imap", "enabled": false, "settings": M{"host": "user.test"}}, 201)
	s.Config.Preconfiguration.Sources = []config.SourceDefaults{{ID: "existing", Label: "File label", SourceType: "imap", Settings: config.Object{"host": "file.test"}}}
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	rows := call(t, s, "GET", "/api/v1/admin/sources", nil, 200).([]any)
	if rows[0].(map[string]any)["label"] != "User label" {
		t.Fatal(rows)
	}
}

func TestPreconfigurationSecretsAndReset(t *testing.T) {
	s := testServer(t)
	ctx := context.Background()
	s.Config.Preconfiguration = config.Preconfiguration{SchemaVersion: 1, LLMAPIKey: "file-llm-secret", Sources: []config.SourceDefaults{{ID: "mail", Label: "Mail", SourceType: "imap", Enabled: true, Credential: "file-mail-secret", Settings: config.Object{"host": "mail.test", "port": float64(993), "tls": true, "username": "user"}}}}
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	q := &request{Context: ctx, server: s, db: s.Pool}
	response := call(t, s, "GET", "/api/v1/admin/settings", nil, 200).(map[string]any)
	if response["llm_api_key_configured"] != true {
		t.Fatal(response)
	}
	encoded, _ := json.Marshal(response)
	if strings.Contains(string(encoded), "file-llm-secret") {
		t.Fatal("file secret disclosed")
	}
	source := call(t, s, "GET", "/api/v1/admin/sources", nil, 200).([]any)[0].(map[string]any)
	if source["credential_configured"] != true {
		t.Fatal(source)
	}
	encoded, _ = json.Marshal(source)
	if strings.Contains(string(encoded), "file-mail-secret") {
		t.Fatal("file secret disclosed")
	}
	row := q.get("communication_sources", "mail")
	credential, _ := q.sourceTokens(row)
	if credential != "file-mail-secret" {
		t.Fatal("source did not resolve secret")
	}
	if str(q.llmConfig(), "api_key") != "file-llm-secret" {
		t.Fatal("LLM did not resolve secret")
	}
	call(t, s, "PUT", "/api/v1/admin/sources/mail", source, 200)
	var settings, encrypted string
	if err := s.Pool.QueryRow(ctx, "SELECT settings::text,coalesce(credential_encrypted,'') FROM communication_sources WHERE id='mail'").Scan(&settings, &encrypted); err != nil {
		t.Fatal(err)
	}
	if settings != "{}" || encrypted != "" {
		t.Fatal("copied file data", settings, encrypted)
	}
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{}, "clear_llm_api_key": true}, 200)
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{}}, 200)
	if str(q.llmConfig(), "api_key") != "" {
		t.Fatal("clear did not survive another save")
	}
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{}, "llm_api_key": "user-llm-key"}, 200)
	if str(q.llmConfig(), "api_key") != "user-llm-key" {
		t.Fatal("user key ignored")
	}
	obj(source, "settings")["host"] = "replacement.test"
	call(t, s, "PUT", "/api/v1/admin/sources/mail", source, 422)
	source["credential"] = "user-mail-key"
	call(t, s, "PUT", "/api/v1/admin/sources/mail", source, 200)
	credential, _ = q.sourceTokens(q.get("communication_sources", "mail"))
	if credential != "user-mail-key" {
		t.Fatal("user source secret ignored")
	}
}

func TestPreconfigurationEmptyListOverridesAndMissingFile(t *testing.T) {
	s := testServer(t)
	ctx := context.Background()
	person := map[string]any{"name": "Manager", "emails": []any{"manager@example.test"}}
	s.Config.Preconfiguration = config.Preconfiguration{Settings: config.Object{"relationships": config.Object{"managers": []any{person}, "reports": []any{}}}, Sources: []config.SourceDefaults{{ID: "external", Label: "External", SourceType: "external_tasks", Enabled: true}}}
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	q := &request{Context: ctx, server: s, db: s.Pool}
	call(t, s, "PUT", "/api/v1/relationships", M{"managers": []any{}, "reports": []any{}}, 200)
	delta := obj(q.one("SELECT payload FROM system_settings WHERE id=1"), "payload")
	if hash(delta) != hash(M{"relationships": M{"managers": []any{}}}) {
		t.Fatal(delta)
	}
	if managers := obj(q.settings(), "relationships")["managers"].([]any); len(managers) != 0 {
		t.Fatal("empty override lost")
	}
	s.Config.Preconfiguration.Sources = nil
	if err := s.PreparePreconfiguration(ctx); err != nil {
		t.Fatal(err)
	}
	if len(q.rows("SELECT id FROM communication_sources WHERE enabled")) != 0 {
		t.Fatal("missing baseline remains active")
	}
}
