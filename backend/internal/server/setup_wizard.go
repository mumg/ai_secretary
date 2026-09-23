package server

import (
	"context"
	"fmt"
	"strings"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

func setupWizardSteps(p config.Preconfiguration) []config.SetupStep {
	if len(p.SetupWizard.Steps) > 0 {
		return p.SetupWizard.Steps
	}
	steps := make([]config.SetupStep, 0, len(p.Sources)+1)
	for _, source := range p.Sources {
		auth := "password"
		instruction := "Проверьте адрес и имя пользователя, введите пароль, сохраните источник и проверьте подключение."
		switch source.SourceType {
		case "mts_link":
			auth = "token"
			instruction = "Сохраните источник, введите access token или войдите через корпоративный SSO и проверьте подключение."
		case "external_tasks":
			auth = "none"
			instruction = "Включите источник и проверьте его настройку."
		}
		steps = append(steps, config.SetupStep{Type: "source", SourceID: source.ID, Widgets: []string{"source:" + source.ID}, Title: source.Label, Instructions: instruction, Auth: auth})
	}
	steps = append(steps, config.SetupStep{Type: "llm", Widgets: []string{"llm"}, Title: "Модель", Instructions: "Укажите адрес, модель и при необходимости API-ключ. Сохраните настройки и проверьте модель."})
	return steps
}

func setupStepWidgets(step config.SetupStep) []string {
	if len(step.Widgets) > 0 {
		return step.Widgets
	}
	if step.Type == "source" {
		return []string{"source:" + step.SourceID}
	}
	return []string{"llm"}
}

func (q *request) configurationWidgets() M {
	widgets := M{}
	for _, row := range q.rows("SELECT * FROM communication_sources ORDER BY id") {
		id := str(row, "id")
		widgets["source:"+id] = M{"configured": sourceWizardConfigured(q.sourceRead(row, false), ""), "verified": row["last_verified_at"] != nil}
	}
	state := q.rows("SELECT llm_verified,identity_verified FROM setup_wizard_state WHERE id=1")
	verified := len(state) > 0 && boolean(state[0], "llm_verified")
	settings := q.settingsRead()
	identity := obj(obj(settings, "settings"), "identity")
	names, _ := identity["names"].([]any)
	identityConfigured := len(names) > 0
	widgets["identity"] = M{"configured": identityConfigured,
		"verified": identityConfigured && len(state) > 0 && boolean(state[0], "identity_verified")}
	llm := obj(obj(settings, "settings"), "llm")
	configured := str(llm, "base_url") != "" && str(llm, "model") != "" &&
		(str(llm, "provider") != "openai" || boolean(settings, "llm_api_key_configured"))
	widgets["llm"] = M{"configured": configured, "verified": verified}
	return widgets
}

func sourceWizardConfigured(source M, _ string) bool {
	if !boolean(source, "enabled") {
		return false
	}
	switch str(source, "source_type") {
	case "external_tasks":
		return true
	case "imap":
		settings := obj(source, "settings")
		return boolean(source, "credential_configured") && str(settings, "host") != "" && str(settings, "username") != "" && num(settings, "port") > 0
	case "exchange":
		settings := obj(source, "settings")
		return boolean(source, "credential_configured") && str(settings, "ews_url") != "" && str(settings, "primary_smtp_address") != "" && str(settings, "username") != ""
	case "mts_link":
		return boolean(source, "credential_configured") && strings.TrimSpace(str(obj(source, "settings"), "base_url")) != ""
	}
	return false
}

func (q *request) setupWizardStatus() M {
	p := q.server.Config.Preconfiguration
	if p.SchemaVersion != 1 || len(p.Sources) == 0 {
		return M{"required": false, "steps": []M{}}
	}
	steps := []M{}
	statuses := q.configurationWidgets()
	allConfigured := true
	for _, step := range setupWizardSteps(p) {
		item := M{"type": step.Type, "source_id": step.SourceID, "title": step.Title, "instructions": step.Instructions, "auth": step.Auth, "help_url": step.HelpURL}
		widgets := []M{}
		for _, id := range setupStepWidgets(step) {
			status, exists := statuses[id]
			// A deleted file-backed source was intentionally removed by the user.
			if !exists {
				continue
			}
			widget := M{"id": id, "configured": boolean(status.(M), "configured"), "verified": boolean(status.(M), "verified")}
			widgets = append(widgets, widget)
			if strings.HasPrefix(id, "source:") {
				allConfigured = allConfigured && boolean(widget, "configured")
			}
		}
		if len(widgets) == 0 {
			continue
		}
		item["widgets"] = widgets
		if len(widgets) == 1 {
			item["configured"] = widgets[0]["configured"]
		}
		steps = append(steps, item)
	}
	if !allConfigured {
		q.exec(`INSERT INTO setup_wizard_state(id) VALUES(1) ON CONFLICT(id) DO UPDATE
			SET completed=false,llm_verified=false,identity_verified=false,updated_at=now() WHERE setup_wizard_state.completed`)
	}
	state := q.rows("SELECT completed,llm_verified,identity_verified FROM setup_wizard_state WHERE id=1")
	for _, item := range steps {
		for _, widget := range item["widgets"].([]M) {
			if widget["id"] == "llm" {
				widget["verified"] = len(state) > 0 && boolean(state[0], "llm_verified")
			} else if widget["id"] == "identity" {
				widget["verified"] = boolean(widget, "configured") && len(state) > 0 && boolean(state[0], "identity_verified")
			}
		}
	}
	return M{"required": !allConfigured || (len(state) > 0 && !boolean(state[0], "completed")), "steps": steps}
}

func (s *Server) setupWizardReady(ctx context.Context) (ready bool, err error) {
	defer func() {
		if failure := recover(); failure != nil {
			err = fmt.Errorf("setup wizard state unavailable (%T)", failure)
		}
	}()
	q := &request{Context: ctx, db: s.Pool, server: s}
	return !boolean(q.setupWizardStatus(), "required"), nil
}

func (s *Server) setupWizardRoutes() {
	s.route("GET /api/v1/admin/configuration-widgets", false, func(q *request) any { return M{"widgets": q.configurationWidgets()} })
	s.route("GET /api/v1/admin/setup-wizard", false, func(q *request) any { return q.setupWizardStatus() })
	s.route("POST /api/v1/admin/setup-wizard/llm/test", false, func(q *request) any {
		if _, err := q.llm("Ответьте одним словом: готово.", M{"probe": "готово"}, "", nil); err != nil {
			panic(err)
		}
		q.exec("INSERT INTO setup_wizard_state(id,completed,llm_verified) VALUES(1,true,true) ON CONFLICT(id) DO UPDATE SET llm_verified=true,updated_at=now()")
		return M{"status": "ok"}
	})
	s.route("POST /api/v1/admin/setup-wizard/identity/confirm", true, func(q *request) any {
		settings := obj(q.settingsRead(), "settings")
		names, _ := obj(settings, "identity")["names"].([]any)
		if len(names) == 0 {
			fail(409, "Укажите имя и фамилию пользователя")
		}
		q.exec("INSERT INTO setup_wizard_state(id,identity_verified) VALUES(1,true) ON CONFLICT(id) DO UPDATE SET identity_verified=true,updated_at=now()")
		return M{"status": "ok"}
	})
	s.route("POST /api/v1/admin/setup-wizard/finish", true, func(q *request) any {
		status := q.setupWizardStatus()
		for _, item := range status["steps"].([]M) {
			for _, widget := range item["widgets"].([]M) {
				if str(widget, "id") == "identity" && (!boolean(widget, "configured") || !boolean(widget, "verified")) {
					fail(409, "Сначала сохраните сведения о пользователе, руководителях и подчинённых")
				}
				if strings.HasPrefix(str(widget, "id"), "source:") && (!boolean(widget, "configured") || !boolean(widget, "verified")) {
					fail(409, "Сначала настройте и проверьте все источники")
				}
			}
		}
		state := q.rows("SELECT llm_verified FROM setup_wizard_state WHERE id=1 FOR UPDATE")
		if len(state) > 0 {
			if !boolean(state[0], "llm_verified") {
				fail(409, "Сначала проверьте модель")
			}
			q.exec("UPDATE setup_wizard_state SET completed=true,updated_at=now() WHERE id=1")
		}
		return M{"status": "ok"}
	})
}
