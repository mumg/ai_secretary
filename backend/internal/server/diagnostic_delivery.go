package server

import (
	"context"
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/gatewaylink"
)

func (q *request) diagnosticClient() (*gatewaylink.DiagnosticClient, error) {
	data, err := os.ReadFile(filepath.Join(q.server.Config.DataDir, "gateway.json"))
	if err != nil {
		return nil, err
	}
	var settings gatewaySettings
	if err = json.Unmarshal(data, &settings); err != nil {
		return nil, err
	}
	if !settings.Enabled {
		return nil, os.ErrNotExist
	}
	return gatewaylink.NewDiagnosticClient(q.server.gatewaySettingsDir(settings))
}

func (q *request) deliverDiagnostic(id string) M {
	report := q.one("SELECT report_id::text,payload_cipher,payload_sha256,state,response_text,last_error,created_at,updated_at FROM diagnostic_reports WHERE report_id=$1", id)
	if str(report, "state") != "queued" {
		return diagnosticStatus(report)
	}
	client, err := q.diagnosticClient()
	if err == nil {
		defer client.Close()
		var payload string
		payload, err = q.server.Config.Decrypt(str(report, "payload_cipher"))
		if err == nil {
			var state string
			state, err = client.Send(q.Context, id, payload)
			if err == nil {
				if state != "received" && state != "in_review" && state != "resolved" && state != "rejected" {
					state = "received"
				}
				q.exec("UPDATE diagnostic_reports SET state=$2,last_error='',updated_at=now() WHERE report_id=$1", id, state)
			}
		}
	}
	if err != nil {
		q.exec("UPDATE diagnostic_reports SET last_error='Не удалось доставить отчёт; повторите отправку',updated_at=now() WHERE report_id=$1", id)
	}
	return diagnosticStatus(q.one("SELECT report_id::text,state,response_text,last_error,created_at,updated_at FROM diagnostic_reports WHERE report_id=$1", id))
}

func diagnosticStatus(row M) M {
	return M{"report_id": row["report_id"], "state": row["state"], "response": row["response_text"], "last_error": row["last_error"], "created_at": row["created_at"], "updated_at": row["updated_at"]}
}

func technicalSupportPayload(id, problem, expected string) M {
	return M{
		"schema_version": 1,
		"report_id":      id,
		"origin":         M{"kind": "technical_support"},
		"issue":          M{"type": "technical_support", "user_comment": problem, "expected": expected},
		"context":        M{},
		"redaction":      M{"mode": "none", "reason": "user_submitted_without_redaction"},
	}
}

func (s *Server) deliverDiagnosticQueued(id string) {
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
		defer cancel()
		if _, err := s.job(ctx, func(q *request) bool { q.deliverDiagnostic(id); return true }); err != nil {
			slog.Error("diagnostic delivery failed", "report_id", id, "error_type", "background")
		}
	}()
}

func (s *Server) diagnosticNeedsReview(id string, draft diagnosticDraft, payload M, labels map[string]string, reason string) {
	encoded, err := json.MarshalIndent(payload, "", "  ")
	if err == nil && len(encoded) <= 256<<10 && len(payload) > 0 {
		draft.JSON = string(encoded)
		draft.Hash = diagnosticHash(draft.JSON)
		draft.Replacements = labels
	}
	draft.State = "needs_review"
	draft.Error = reason
	draft.Expires = time.Now().Add(24 * time.Hour)
	s.reports.Lock()
	if current, ok := s.reports.drafts[id]; ok && current.State == "sending" {
		s.reports.drafts[id] = draft
	}
	s.reports.Unlock()
}

func (s *Server) sendDiagnosticDraftAsync(id string, edits []any) {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Minute)
	defer cancel()
	s.reports.Lock()
	draft, found := s.reports.drafts[id]
	s.reports.Unlock()
	if !found || draft.State != "sending" {
		return
	}
	var payload M
	if err := json.Unmarshal([]byte(draft.JSON), &payload); err != nil {
		s.diagnosticNeedsReview(id, draft, M{}, draft.Replacements, "Не удалось прочитать черновик. Откройте его и повторите отправку.")
		return
	}
	labels := make(map[string]string, len(draft.Replacements))
	for name, marker := range draft.Replacements {
		labels[name] = marker
	}
	defer func() {
		if recovered := recover(); recovered != nil {
			slog.Error("diagnostic background check failed", "error_type", "panic")
			s.diagnosticNeedsReview(id, draft, payload, labels, "Проверка обращения не завершилась. Откройте черновик, проверьте поля и повторите отправку.")
		}
	}()
	diagnosticApplyEdits(payload, edits)
	issue := obj(payload, "issue")
	if strings.TrimSpace(str(issue, "user_comment")) == "" || strings.TrimSpace(str(issue, "expected")) == "" {
		diagnosticRedact(payload, labels)
		s.diagnosticNeedsReview(id, draft, payload, labels, "Заполните полученное некорректное и ожидаемое поведение, затем повторите отправку.")
		return
	}
	before := diagnosticFieldsHash(payload)
	if before != draft.ValidatedFieldsHash {
		var editedText strings.Builder
		for _, field := range diagnosticFields(payload) {
			editedText.WriteString(str(field, "value") + "\n")
		}
		q := &request{Context: ctx, db: s.Pool, server: s}
		if !q.diagnosticEntities(editedText.String(), labels) {
			diagnosticRedact(payload, labels)
			s.diagnosticNeedsReview(id, draft, payload, labels, "ИИ не смог проверить обезличивание. Проверьте поля черновика и повторите отправку.")
			return
		}
		diagnosticRedact(payload, labels)
		if before != diagnosticFieldsHash(payload) {
			draft.ValidatedFieldsHash = diagnosticFieldsHash(payload)
			s.diagnosticNeedsReview(id, draft, payload, labels, "При проверке дополнительно скрыты личные данные. Просмотрите обновлённую версию и повторно подтвердите отправку.")
			return
		}
	}
	encoded := string(must(json.MarshalIndent(payload, "", "  ")))
	if len(encoded) > 256<<10 {
		s.diagnosticNeedsReview(id, draft, payload, labels, "Обращение слишком большое. Сократите текст в черновике и повторите отправку.")
		return
	}
	reportID := str(payload, "report_id")
	hash := diagnosticHash(encoded)
	cipher := must(s.Config.Encrypt(encoded))
	_, err := s.job(ctx, func(q *request) bool {
		q.exec(`INSERT INTO diagnostic_reports(report_id,payload_cipher,payload_sha256,state) VALUES($1,$2,$3,'queued') ON CONFLICT(report_id) DO NOTHING`, reportID, cipher, hash)
		stored := q.one("SELECT payload_sha256 FROM diagnostic_reports WHERE report_id=$1", reportID)
		if str(stored, "payload_sha256") != hash {
			fail(409, "Report ID conflict")
		}
		return true
	})
	if err != nil {
		s.diagnosticNeedsReview(id, draft, payload, labels, "Не удалось поставить обращение в очередь доставки. Повторите отправку из черновика.")
		return
	}
	s.reports.Lock()
	delete(s.reports.drafts, id)
	s.reports.Unlock()
	s.deliverDiagnosticQueued(reportID)
}

func (s *Server) diagnosticDeliveryRoutes() {
	s.route("POST /api/v1/diagnostic-reports/support", false, func(q *request) any {
		body := q.body()
		problem, expected := strings.TrimSpace(str(body, "problem")), strings.TrimSpace(str(body, "expected"))
		if problem == "" || expected == "" || len([]rune(problem)) > 4000 || len([]rune(expected)) > 4000 {
			fail(422, "Заполните проблему и ожидаемый результат (не более 4000 символов каждый)")
		}
		if !boolean(body, "consent_without_redaction") {
			fail(422, "Подтвердите отправку без обезличивания")
		}
		id := newID()
		payload := technicalSupportPayload(id, problem, expected)
		encoded := string(must(json.Marshal(payload)))
		cipher := must(s.Config.Encrypt(encoded))
		q.exec(`INSERT INTO diagnostic_reports(report_id,payload_cipher,payload_sha256,state) VALUES($1,$2,$3,'queued')`, id, cipher, diagnosticHash(encoded))
		s.deliverDiagnosticQueued(id)
		q.status = 202
		return M{"report_id": id, "state": "queued"}
	})
	s.route("POST /api/v1/diagnostic-reports/drafts/{id}/send", false, func(q *request) any {
		id := q.id("id")
		body := q.body()
		s.reports.Lock()
		d, ok := s.reports.drafts[id]
		if !ok || time.Now().After(d.Expires) {
			s.reports.Unlock()
			fail(404, "Diagnostic draft not found")
		}
		if d.State != "ready" && d.State != "needs_review" && d.State != "sending" {
			s.reports.Unlock()
			fail(409, "Diagnostic draft is not ready")
		}
		if str(body, "payload_sha256") != d.Hash {
			s.reports.Unlock()
			fail(409, "Report preview changed")
		}
		var payload M
		if err := json.Unmarshal([]byte(d.JSON), &payload); err != nil {
			s.reports.Unlock()
			check(err)
		}
		reportID := str(payload, "report_id")
		if d.State == "sending" {
			s.reports.Unlock()
			q.status = 202
			return M{"draft_id": id, "report_id": reportID, "state": "sending"}
		}
		edits, ok := body["edits"].([]any)
		if !ok || len(edits) > 250 {
			s.reports.Unlock()
			fail(422, "Expected report field edits")
		}
		d.State = "sending"
		d.Error = ""
		d.Expires = time.Now().Add(24 * time.Hour)
		s.reports.drafts[id] = d
		s.reports.Unlock()
		go s.sendDiagnosticDraftAsync(id, edits)
		q.status = 202
		return M{"draft_id": id, "report_id": reportID, "state": "sending"}
	})
	s.route("GET /api/v1/diagnostic-reports", false, func(q *request) any {
		q.exec("DELETE FROM diagnostic_reports WHERE created_at < now() - interval '90 days'")
		if client, err := q.diagnosticClient(); err == nil {
			if statuses, err := client.Statuses(q.Context); err == nil {
				for id, status := range statuses {
					if status.State == "received" || status.State == "in_review" || status.State == "resolved" || status.State == "rejected" {
						q.exec("UPDATE diagnostic_reports SET state=$2,response_text=$3,updated_at=now() WHERE report_id=$1 AND (state<>$2 OR response_text<>$3)", id, status.State, status.Response)
					}
				}
			}
			client.Close()
		}
		rows := q.rows("SELECT report_id::text,state,response_text,last_error,created_at,updated_at FROM diagnostic_reports ORDER BY created_at DESC LIMIT 100")
		items := []M{}
		for _, row := range rows {
			items = append(items, diagnosticStatus(row))
		}
		return M{"items": items, "drafts": s.diagnosticDraftStatuses()}
	})
	s.route("GET /api/v1/diagnostic-reports/{id}", false, func(q *request) any {
		id := q.id("id")
		row := q.one("SELECT report_id::text,state,response_text,last_error,created_at,updated_at FROM diagnostic_reports WHERE report_id=$1", id)
		if str(row, "state") != "queued" {
			if client, err := q.diagnosticClient(); err == nil {
				status, statusErr := client.Status(q.Context, id)
				client.Close()
				if statusErr == nil && (status.State == "received" || status.State == "in_review" || status.State == "resolved" || status.State == "rejected") && (status.State != str(row, "state") || status.Response != str(row, "response_text")) {
					q.exec("UPDATE diagnostic_reports SET state=$2,response_text=$3,updated_at=now() WHERE report_id=$1", id, status.State, status.Response)
					row["state"], row["response_text"] = status.State, status.Response
				}
			}
		}
		return diagnosticStatus(row)
	})
	s.route("GET /api/v1/diagnostic-reports/details/{id}", false, func(q *request) any {
		row := q.one("SELECT report_id::text,payload_cipher,state,response_text,last_error,created_at,updated_at FROM diagnostic_reports WHERE report_id=$1", q.id("id"))
		plaintext := must(q.server.Config.Decrypt(str(row, "payload_cipher")))
		var payload M
		check(json.Unmarshal([]byte(plaintext), &payload))
		result := diagnosticStatus(row)
		result["fields"] = diagnosticFields(payload)
		result["issue_type"] = obj(payload, "issue")["type"]
		return result
	})
	s.route("DELETE /api/v1/diagnostic-reports/{id}", false, func(q *request) any {
		id := q.id("id")
		q.one("SELECT report_id::text FROM diagnostic_reports WHERE report_id=$1", id)
		client, err := q.diagnosticClient()
		if err != nil {
			fail(503, "Шлюз недоступен. Обращение не удалено; повторите попытку позже")
		}
		defer client.Close()
		if err := client.Delete(q.Context, id); err != nil {
			fail(502, "Не удалось удалить обращение на шлюзе; повторите попытку позже")
		}
		q.exec("DELETE FROM diagnostic_reports WHERE report_id=$1", id)
		q.status = 204
		return nil
	})
	s.route("POST /api/v1/diagnostic-reports/{id}/retry", false, func(q *request) any {
		id := q.id("id")
		return q.deliverDiagnostic(id)
	})
}
