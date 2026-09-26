package server

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

func TestDiagnosticFieldsAndRedaction(t *testing.T) {
	payload := M{
		"schema_version": 1,
		"report_id":      "7f8d2c31-0e54-4b09-a206-8317ec5c949a",
		"origin":         M{"kind": "conversation_event", "ref": "E1"},
		"issue":          M{"user_comment": "Письмо для Система Альфа", "expected": "Позвонить ivan@example.test", "observed": []any{}},
		"context":        M{"anchor": "E1", "events": []any{map[string]any{"author": "Иван Петров", "subject": "Система Альфа", "body": "Иван Петров: доступ к Система Альфа на https://internal.example.test"}}},
	}
	labels := map[string]string{"Иван Петров": "[PERSON_1]", "Система Альфа": "[SYSTEM_1]"}
	diagnosticRedact(payload, labels)
	data, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{"Иван Петров", "Система Альфа", "ivan@example.test", "https://internal.example.test"} {
		if strings.Contains(string(data), raw) {
			t.Fatalf("unredacted %q in %s", raw, data)
		}
	}
	if str(payload, "report_id") != "7f8d2c31-0e54-4b09-a206-8317ec5c949a" {
		t.Fatal("report identity was changed")
	}
	fields := diagnosticFields(payload)
	if len(fields) != 5 {
		t.Fatalf("got %d editable fields", len(fields))
	}
	diagnosticApplyEdits(payload, []any{map[string]any{"path": "/context/events/0/body", "value": "Письмо от ivan@example.test"}})
	diagnosticRedact(payload, labels)
	if body := str(M(obj(payload, "context")["events"].([]any)[0].(map[string]any)), "body"); strings.Contains(body, "ivan@example.test") {
		t.Fatal("edit introduced a raw email")
	}
}

func TestDiagnosticReviewedFieldsDoNotNeedRepeatedRedaction(t *testing.T) {
	payload := M{"issue": M{"user_comment": "Ошибка [SYSTEM_1]", "expected": "Исправить"}, "context": M{"events": []any{map[string]any{"body": "Письмо от [PERSON_1]"}}}}
	validated := diagnosticFieldsHash(payload)
	diagnosticApplyEdits(payload, []any{
		map[string]any{"path": "/issue/user_comment", "value": "Ошибка [SYSTEM_1]"},
		map[string]any{"path": "/context/events/0/body", "value": "Письмо от [PERSON_1]"},
	})
	if diagnosticFieldsHash(payload) != validated {
		t.Fatal("unchanged reviewed fields unexpectedly require another LLM check")
	}
	diagnosticApplyEdits(payload, []any{map[string]any{"path": "/issue/user_comment", "value": "Ошибка в новой системе"}})
	if diagnosticFieldsHash(payload) == validated {
		t.Fatal("edited fields must be checked again")
	}
	if got := diagnosticMarkerPattern.ReplaceAllString("Письмо от [PERSON_1] и [ПОЛЬЗОВАТЕЛЬ]", " "); strings.Contains(got, "PERSON_1") || strings.Contains(got, "ПОЛЬЗОВАТЕЛЬ") {
		t.Fatalf("redaction markers were sent to entity detection: %q", got)
	}
}

func TestDiagnosticProcessingResponseDoesNotRevealInput(t *testing.T) {
	d := diagnosticDraft{State: "processing", Created: time.Now(), Expires: time.Now().Add(time.Hour), Input: M{"user_comment": "Секретный текст"}}
	response := diagnosticResponse("draft-id", d)
	encoded, err := json.Marshal(response)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "Секретный текст") || strings.Contains(string(encoded), "fields") {
		t.Fatal("processing response disclosed source data")
	}
}

func TestDiagnosticSendQueuesAndLeavesDraftWhenClarificationNeeded(t *testing.T) {
	s := New(nil, config.Config{WebDir: "../../web"}, "0.7.48")
	id := "7f8d2c31-0e54-4b09-a206-8317ec5c949a"
	payload := M{
		"report_id": "8a164ef4-467c-4cb2-a663-4250713a137b",
		"issue":     M{"user_comment": "Задача назначена ошибочно", "expected": "", "observed": []any{}},
		"context":   M{"events": []any{map[string]any{"body": "[PERSON_1] просит ответ"}}},
	}
	encoded := string(must(json.Marshal(payload)))
	s.reports.drafts = map[string]diagnosticDraft{id: {State: "ready", JSON: encoded, Hash: diagnosticHash(encoded), Created: time.Now(), Expires: time.Now().Add(time.Hour)}}
	body := string(must(json.Marshal(M{"payload_sha256": diagnosticHash(encoded), "edits": []any{map[string]any{"path": "/issue/user_comment", "value": "Задача назначена ошибочно"}}})))
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("POST", "/api/v1/diagnostic-reports/drafts/"+id+"/send", bytes.NewBufferString(body)))
	if w.Code != 202 || !strings.Contains(w.Body.String(), `"state":"sending"`) {
		t.Fatalf("send did not return an immediate queue receipt: %d %s", w.Code, w.Body.String())
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		s.reports.Lock()
		draft := s.reports.drafts[id]
		s.reports.Unlock()
		if draft.State == "needs_review" {
			if !strings.Contains(draft.Error, "Заполните") || len(diagnosticResponse(id, draft)["fields"].([]M)) == 0 {
				t.Fatalf("draft lacks actionable guidance or editable fields: %+v", draft)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("background check did not return the draft for clarification")
}

func TestReportsHaveDedicatedWebPage(t *testing.T) {
	s := New(nil, config.Config{WebDir: "../../web"}, "0.7.39")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("GET", "/app/reports", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), "reports.js") {
		t.Fatalf("dedicated reports page: %d %s", w.Code, w.Body.String())
	}
	if !strings.Contains(w.Body.String(), "reports-new-support") || !strings.Contains(w.Body.String(), "support-problem") {
		t.Fatal("technical support form is missing from reports page")
	}
	w = httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("GET", "/app/report/new", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), "report-new.js") || !strings.Contains(w.Body.String(), "report-message-pane") {
		t.Fatalf("full-page report editor: %d %s", w.Code, w.Body.String())
	}
}

func TestTechnicalSupportPayloadAndConsent(t *testing.T) {
	id := "7f8d2c31-0e54-4b09-a206-8317ec5c949a"
	payload := technicalSupportPayload(id, "Не открываются настройки", "Настройки должны открываться")
	if str(payload, "report_id") != id || str(obj(payload, "issue"), "type") != "technical_support" || str(obj(payload, "redaction"), "mode") != "none" {
		t.Fatalf("unexpected support payload: %+v", payload)
	}
	if str(obj(payload, "issue"), "user_comment") != "Не открываются настройки" || str(obj(payload, "issue"), "expected") != "Настройки должны открываться" || len(obj(payload, "context")) != 0 {
		t.Fatalf("support fields were changed or context was added: %+v", payload)
	}
	s := New(nil, config.Config{WebDir: "../../web"}, "0.7.50")
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("POST", "/api/v1/diagnostic-reports/support", bytes.NewBufferString(`{"problem":"Не открываются настройки","expected":"Настройки должны открываться"}`)))
	if w.Code != 422 || !strings.Contains(w.Body.String(), "Подтвердите отправку") {
		t.Fatalf("support request without consent was accepted: %d %s", w.Code, w.Body.String())
	}
}

func TestDraftStatusEndpointHighlightsReadyWithoutSourceText(t *testing.T) {
	s := New(nil, config.Config{WebDir: "../../web"}, "0.7.39")
	s.reports.drafts = map[string]diagnosticDraft{
		"draft-id": {State: "ready", Created: time.Now(), Expires: time.Now().Add(time.Hour), Input: M{"user_comment": "Секретный текст"}},
	}
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest("GET", "/api/v1/diagnostic-reports/drafts", nil))
	if w.Code != 200 || !strings.Contains(w.Body.String(), `"state":"ready"`) || strings.Contains(w.Body.String(), "Секретный текст") {
		t.Fatalf("draft status response: %d %s", w.Code, w.Body.String())
	}
}

func TestDiagnosticSignatureIsRedacted(t *testing.T) {
	body := "Обсуждали Selfe.\nС уважением,\nСергей Сурков\nCluster Program lead (технологическая стратегия)\nИТ-кластер «Продажи и Обслуживание»\nМТС Веб Сервисы"
	event := M{"author": "Другой отправитель", "subject": "Письмо", "body": body}
	labels := diagnosticLabels(event, []M{event}, nil, nil, nil, nil)
	labels["Selfe"] = "[SYSTEM_1]" // Entity returned by the structured LLM.
	payload := M{"context": M{"events": []any{map[string]any{"body": body}}}}
	diagnosticRedact(payload, labels)
	encoded := string(must(json.Marshal(payload)))
	for _, private := range []string{"Selfe", "Сергей Сурков", "Cluster Program lead", "ИТ-кластер", "МТС Веб Сервисы"} {
		if strings.Contains(encoded, private) {
			t.Fatalf("signature leaked: %s", private)
		}
	}
}

func TestDiagnosticSenderAndRecipientsUseRelationshipRoles(t *testing.T) {
	event := M{
		"author": "Руководитель <boss@example.test>",
		"participants": []any{
			map[string]any{"name": "Максим Муратов", "address": "me@example.test", "role": "to"},
			map[string]any{"name": "Подчинённый", "address": "report@example.test", "role": "cc"},
		},
		"body": "Руководитель просит Максим Муратов ответить Подчинённый.",
	}
	relationships := M{
		"managers": []any{map[string]any{"name": "Руководитель", "emails": []any{"boss@example.test"}}},
		"reports":  []any{map[string]any{"name": "Подчинённый", "emails": []any{"report@example.test"}}},
	}
	labels := diagnosticLabels(event, []M{event}, nil, []string{"Максим Муратов"}, []string{"me@example.test"}, relationships)
	processed := M{"author": event["author"], "recipients": diagnosticRecipients(event), "body": event["body"]}
	diagnosticRedact(processed, labels)
	if str(processed, "author") != "[РУКОВОДИТЕЛЬ]" {
		t.Fatalf("sender: %q", processed["author"])
	}
	if got := str(processed, "recipients"); got != "TO: [ПОЛЬЗОВАТЕЛЬ]; CC: [ПОДЧИНЕННЫЙ]" {
		t.Fatalf("recipients: %q", got)
	}
	if got := str(processed, "body"); !strings.Contains(got, "[РУКОВОДИТЕЛЬ]") || !strings.Contains(got, "[ПОЛЬЗОВАТЕЛЬ]") || !strings.Contains(got, "[ПОДЧИНЕННЫЙ]") {
		t.Fatalf("body: %q", got)
	}
}

func TestDiagnosticAmbiguousNameDoesNotAcquireRole(t *testing.T) {
	event := M{"author": "Алексей Иванов"}
	relationships := M{
		"managers": []any{map[string]any{"name": "Алексей Иванов", "emails": []any{"boss@example.test"}}},
		"reports":  []any{map[string]any{"name": "Алексей Иванов", "emails": []any{"report@example.test"}}},
	}
	labels := diagnosticLabels(event, []M{event}, nil, nil, nil, relationships)
	if got := diagnosticRedactText("Алексей Иванов", labels); got == "[РУКОВОДИТЕЛЬ]" || got == "[ПОДЧИНЕННЫЙ]" {
		t.Fatalf("ambiguous name assigned a role: %q", got)
	}
}

func TestDiagnosticOfficeAddressIsRedactedAsOneField(t *testing.T) {
	address := "г. Москва, проспект Андропова, д. 18, корп.  9 (БЦ Декарт), этаж  9, 9.02"
	text := "Встреча по адресу: " + address + "\nПожалуйста, подтвердите участие."
	redacted := diagnosticRedactText(text, map[string]string{})
	if !strings.Contains(redacted, "[ADDRESS_1]") {
		t.Fatalf("address marker is missing: %q", redacted)
	}
	for _, fragment := range []string{"Москва", "Андропова", "Декарт", "9.02"} {
		if strings.Contains(redacted, fragment) {
			t.Fatalf("address fragment %q leaked: %q", fragment, redacted)
		}
	}
	if !strings.Contains(redacted, "Пожалуйста, подтвердите участие.") {
		t.Fatalf("unrelated message text was removed: %q", redacted)
	}
}

func TestDiagnosticReviewDoesNotSendOriginal(t *testing.T) {
	d := diagnosticDraft{State: "ready", Original: M{"body": "Сергей Сурков"}, Processed: []M{{"title": "Selfe"}}, JSON: `{"context":{"events":[{"body":"[PERSON_1]"}]},"issue":{"user_comment":"Ошибка","expected":"Нет задачи"}}`}
	ordinary := string(must(json.Marshal(diagnosticResponse("draft", d))))
	if strings.Contains(ordinary, "Сергей Сурков") || strings.Contains(ordinary, "Selfe") {
		t.Fatal("original escaped through ordinary draft response")
	}
	review := diagnosticReviewResponse("draft", d)
	if str(obj(review, "original"), "body") != "Сергей Сурков" {
		t.Fatal("local review has no original")
	}
	if llmSchemas["DiagnosticRedaction"].Validate(map[string]any{"entities": []any{map[string]any{"text": "Selfe", "type": "SYSTEM"}}}) != nil {
		t.Fatal("redaction schema rejects valid entity")
	}
}

func TestDiagnosticAllowsReviewOfLongEmailBody(t *testing.T) {
	longBody := strings.Repeat("Письмо для проверки. ", 600)
	payload := M{"context": M{"events": []any{map[string]any{"body": longBody}}}}
	diagnosticApplyEdits(payload, []any{map[string]any{"path": "/context/events/0/body", "value": longBody}})
	if body := str(M(obj(payload, "context")["events"].([]any)[0].(map[string]any)), "body"); body != longBody {
		t.Fatal("long email body changed during review")
	}
}
