package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

func TestSetupWizardSkipsConfiguredSources(t *testing.T) {
	s := testServer(t)
	if result := call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any); result["required"] != false {
		t.Fatal(result)
	}
	s.Config.Preconfiguration = config.Preconfiguration{SchemaVersion: 1, Sources: []config.SourceDefaults{{ID: "mail", Label: "Mail", SourceType: "imap", Settings: config.Object{"host": "imap.example.test", "port": float64(993), "tls": true}}}, SetupWizard: config.SetupWizard{Steps: []config.SetupStep{{Type: "source", SourceID: "mail", Title: "Mail", Instructions: "Enter password", Auth: "password"}, {Type: "llm", Title: "Model", Instructions: "Enter API token"}}}}
	if err := s.PreparePreconfiguration(context.Background()); err != nil {
		t.Fatal(err)
	}
	result := call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	if result["required"] != true || len(result["steps"].([]any)) != 2 {
		t.Fatal(result)
	}
	if ready, err := s.setupWizardReady(context.Background()); err != nil || ready {
		t.Fatal("worker started before wizard completion", ready, err)
	}
	source := call(t, s, "GET", "/api/v1/admin/sources", nil, 200).([]any)[0].(map[string]any)
	source["enabled"] = true
	source["credential"] = "private-password"
	obj(source, "settings")["username"] = "me@example.test"
	call(t, s, "PUT", "/api/v1/admin/sources/mail", source, 200)
	// The connection probe records verification before the wizard can finish.
	q := &request{Context: context.Background(), db: s.Pool, server: s}
	q.exec("UPDATE communication_sources SET last_verified_at=now() WHERE id='mail'")
	result = call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	if result["required"] != true || result["steps"].([]any)[0].(map[string]any)["configured"] != true {
		t.Fatal(result)
	}
	call(t, s, "POST", "/api/v1/admin/setup-wizard/finish", M{}, 409)
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, M{"message": M{"content": "готово"}})
	}))
	defer model.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "ollama", "base_url": model.URL, "model": "test-model"}}}, 200)
	call(t, s, "POST", "/api/v1/admin/setup-wizard/llm/test", M{}, 200)
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"model": "changed-model"}}}, 200)
	call(t, s, "POST", "/api/v1/admin/setup-wizard/finish", M{}, 409)
	call(t, s, "POST", "/api/v1/admin/setup-wizard/llm/test", M{}, 200)
	call(t, s, "POST", "/api/v1/admin/setup-wizard/finish", M{}, 200)
	result = call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	if result["required"] != false {
		t.Fatal(result)
	}
	if ready, err := s.setupWizardReady(context.Background()); err != nil || !ready {
		t.Fatal("worker did not start after wizard completion", ready, err)
	}
	if encoded, _ := json.Marshal(result); strings.Contains(string(encoded), "private-password") {
		t.Fatal("wizard leaked source credential")
	}
	call(t, s, "DELETE", "/api/v1/admin/sources/mail", nil, 204)
	result = call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	if result["required"] != false || len(result["steps"].([]any)) != 1 {
		t.Fatal("removed source returned to wizard", result)
	}
}

func TestSetupWizardSkipsFullyConfiguredFile(t *testing.T) {
	s := testServer(t)
	s.Config.Preconfiguration = config.Preconfiguration{SchemaVersion: 1, Sources: []config.SourceDefaults{{ID: "mail", Label: "Mail", SourceType: "imap", Enabled: true, Credential: "file-secret", Settings: config.Object{"host": "imap.example.test", "port": float64(993), "tls": true, "username": "me@example.test"}}}}
	if err := s.PreparePreconfiguration(context.Background()); err != nil {
		t.Fatal(err)
	}
	if result := call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any); result["required"] != false {
		t.Fatal(result)
	}
	if ready, err := s.setupWizardReady(context.Background()); err != nil || !ready {
		t.Fatal("worker did not start for a configured file", ready, err)
	}
}

func TestSetupWizardIdentityRequiresSaveAndRecheckAfterChanges(t *testing.T) {
	s := testServer(t)
	s.Config.Preconfiguration = config.Preconfiguration{SchemaVersion: 1,
		Sources: []config.SourceDefaults{{ID: "mail", Label: "Mail", SourceType: "imap", Settings: config.Object{"host": "imap.example.test", "port": float64(993), "tls": true}}},
		SetupWizard: config.SetupWizard{Steps: []config.SetupStep{
			{Widgets: []string{"identity"}, Instructions: "Enter people"},
			{Widgets: []string{"source:mail"}, Instructions: "Enter password"},
			{Widgets: []string{"llm"}, Instructions: "Enter model"},
		}},
	}
	if err := s.PreparePreconfiguration(context.Background()); err != nil {
		t.Fatal(err)
	}
	status := call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	first := status["steps"].([]any)[0].(map[string]any)["widgets"].([]any)[0].(map[string]any)
	if first["id"] != "identity" || first["verified"] != false {
		t.Fatal(status)
	}
	call(t, s, "POST", "/api/v1/admin/setup-wizard/identity/confirm", M{}, 409)
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{
		"identity": M{"names": []string{"Иван Иванов"}},
		"relationships": M{"managers": []M{{"name": "Руководитель", "emails": []string{"boss@example.test"}}},
			"reports": []M{{"name": "Подчинённый", "emails": []string{"report@example.test"}}}},
	}}, 200)
	call(t, s, "POST", "/api/v1/admin/setup-wizard/identity/confirm", M{}, 200)
	status = call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	first = status["steps"].([]any)[0].(map[string]any)["widgets"].([]any)[0].(map[string]any)
	if first["configured"] != true || first["verified"] != true {
		t.Fatal(status)
	}
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"relationships": M{"reports": []M{}}}}, 200)
	status = call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	first = status["steps"].([]any)[0].(map[string]any)["widgets"].([]any)[0].(map[string]any)
	if first["verified"] != false {
		t.Fatal("identity verification should reset after employee edits", status)
	}
}

func TestSetupWizardModelProbe(t *testing.T) {
	s := testServer(t)
	model := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, M{"message": M{"content": "готово"}})
	}))
	defer model.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"provider": "ollama", "base_url": model.URL, "model": "test-model"}}}, 200)
	result := call(t, s, "POST", "/api/v1/admin/setup-wizard/llm/test", M{}, 200).(map[string]any)
	if result["status"] != "ok" {
		t.Fatal(result)
	}
}

func TestSetupWizardMTSManualTokenFallback(t *testing.T) {
	source := M{"source_type": "mts_link", "enabled": true, "credential_configured": true,
		"refresh_token_configured": false, "settings": M{"base_url": "https://gw.mts-link.ru"}}
	if !sourceWizardConfigured(source, "sso") {
		t.Fatal("working manual access token must be accepted as the offered SSO fallback")
	}
	source["enabled"] = false
	if sourceWizardConfigured(source, "sso") {
		t.Fatal("disabled source must not complete the wizard")
	}
}

func TestConfigurationWidgetVerificationInvalidatesOnSourceChange(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "tasks", "label": "Tasks", "source_type": "external_tasks", "enabled": true, "settings": M{}}, 201)
	read := func() map[string]any {
		response := call(t, s, "GET", "/api/v1/admin/configuration-widgets", nil, 200).(map[string]any)
		return response["widgets"].(map[string]any)["source:tasks"].(map[string]any)
	}
	if status := read(); status["configured"] != true || status["verified"] != false {
		t.Fatal(status)
	}
	call(t, s, "POST", "/api/v1/admin/sources/tasks/test", M{}, 200)
	if status := read(); status["configured"] != true || status["verified"] != true {
		t.Fatal(status)
	}
	call(t, s, "PUT", "/api/v1/admin/sources/tasks", M{"id": "tasks", "label": "Tasks", "source_type": "external_tasks", "enabled": false, "settings": M{}}, 200)
	if status := read(); status["configured"] != false || status["verified"] != false {
		t.Fatal(status)
	}
}

func TestSetupWizardGroupsMultipleWidgetsOnOneStep(t *testing.T) {
	s := testServer(t)
	s.Config.Preconfiguration = config.Preconfiguration{SchemaVersion: 1,
		Sources: []config.SourceDefaults{
			{ID: "first", Label: "First", SourceType: "external_tasks", Enabled: false},
			{ID: "second", Label: "Second", SourceType: "external_tasks", Enabled: false},
		},
		SetupWizard: config.SetupWizard{Steps: []config.SetupStep{
			{Widgets: []string{"source:first", "source:second"}, Title: "Sources", Instructions: "Connect both"},
			{Widgets: []string{"llm"}, Title: "Model", Instructions: "Check the model"},
		}},
	}
	if err := s.PreparePreconfiguration(context.Background()); err != nil {
		t.Fatal(err)
	}
	status := call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any)
	steps := status["steps"].([]any)
	if status["required"] != true || len(steps) != 2 {
		t.Fatal(status)
	}
	first := steps[0].(map[string]any)["widgets"].([]any)
	if len(first) != 2 || first[0].(map[string]any)["id"] != "source:first" || first[1].(map[string]any)["id"] != "source:second" {
		t.Fatal(first)
	}
}

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
	s.Config.Preconfiguration.Sources[0].Settings["initial_assignment_days"] = float64(7)

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
	if wizard := call(t, s, "GET", "/api/v1/admin/setup-wizard", nil, 200).(map[string]any); wizard["required"] != false {
		t.Fatal("fully configured file should not open wizard", wizard)
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
