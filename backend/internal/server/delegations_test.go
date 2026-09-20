package server

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestDelegationLifecycleSearchAndReplay(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false, "settings": M{"username": "me@example.test"}}, 201)
	body := "Иван, подготовь отчёт о поставках."
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "out", "event_type": "email", "direction": "OUTGOING", "thread_external_id": "thread", "author": "me@example.test", "body": body, "occurred_at": "2026-09-17T10:00:00Z"}, 202).(map[string]any)
	candidate := M{"delegation_id": "", "is_new": true, "title": "Подготовить отчёт", "description": "Поставки оборудования", "expected_result": "Таблица задержек", "assignee_name": "Иван Петров", "assignee_email": "ivan@example.test", "due_at": nil, "evidence": body, "confidence": 0.99, "status": "ASSIGNED"}
	run := func(event M, c M) {
		t.Helper()
		_, err := s.job(context.Background(), func(q *request) bool {
			existing := q.rows("SELECT * FROM delegations")
			q.applyDelegationAnalysis(event, M{"items": []any{map[string]any(c)}}, existing, str(event, "body"))
			return true
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	run(event, candidate)
	run(event, candidate)
	rows := call(t, s, "GET", "/api/v1/delegations?q="+url.QueryEscape("поставка")+"&assignee=ivan%40example.test", nil, 200).(map[string]any)["items"].([]any)
	if len(rows) != 1 {
		t.Fatal(rows)
	}
	d := rows[0].(map[string]any)
	id := str(d, "id")
	if tasks := call(t, s, "GET", "/api/v1/tasks", nil, 200).([]any); len(tasks) != 0 {
		t.Fatal("delegation leaked into tasks")
	}
	if rows := call(t, s, "GET", "/api/v1/delegations?q="+url.QueryEscape("таблицы"), nil, 200).(map[string]any)["items"].([]any); len(rows) != 1 {
		t.Fatal("expected result is not indexed")
	}
	if rows := call(t, s, "GET", "/api/v1/delegations?assignee=other%40example.test", nil, 200).(map[string]any)["items"].([]any); len(rows) != 0 {
		t.Fatal("assignee filter")
	}
	incoming := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "in", "event_type": "email", "direction": "INCOMING", "thread_external_id": "thread", "author": "ivan@example.test", "body": "Отчёт готов, направляю результат.", "occurred_at": "2026-09-18T10:00:00Z"}, 202).(map[string]any)
	c := M{"delegation_id": id, "is_new": false, "confidence": 0.99, "evidence": incoming["body"], "status": "IN_REVIEW"}
	run(incoming, c)
	detail := call(t, s, "GET", "/api/v1/delegations/"+id, nil, 200).(map[string]any)
	if detail["status"] != "IN_REVIEW" || len(detail["history"].([]any)) != 2 {
		t.Fatal(detail)
	}
	call(t, s, "PATCH", "/api/v1/delegations/"+id, M{"status": "IN_PROGRESS"}, 200)
	run(incoming, c)
	if d := call(t, s, "GET", "/api/v1/delegations/"+id, nil, 200).(map[string]any); d["status"] != "IN_PROGRESS" || len(d["history"].([]any)) != 3 {
		t.Fatal("manual change overwritten", d)
	}
	c["status"] = "COMPLETED"
	incoming["occurred_at"] = time.Now().Add(time.Hour).Format(time.RFC3339)
	run(incoming, c)
	if d := call(t, s, "GET", "/api/v1/delegations/"+id, nil, 200).(map[string]any); d["status"] != "IN_PROGRESS" {
		t.Fatal("AI accepted result")
	}
	call(t, s, "PATCH", "/api/v1/delegations/"+id, M{"status": "COMPLETED"}, 200)
	call(t, s, "PATCH", "/api/v1/delegations/"+id, M{"status": "INVALID"}, 422)
	_, err := s.job(context.Background(), func(q *request) bool {
		_, found := q.retrieveArchive(M{"query": "поручения Петров отчёт"})
		if len(found) != 1 || found[0]["record_kind"] != "delegation" {
			t.Fatal("chat retrieval", found)
		}
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
}
func TestRelationshipsValidationAndPersistence(t *testing.T) {
	s := testServer(t)
	value := M{"managers": []M{{"name": "Начальник", "emails": []string{"BOSS@example.test", "boss2@example.test"}}}, "reports": []M{}}
	saved := call(t, s, "PUT", "/api/v1/relationships", value, 200).(map[string]any)
	if saved["managers"].([]any)[0].(map[string]any)["emails"].([]any)[0] != "boss@example.test" {
		t.Fatal(saved)
	}
	call(t, s, "GET", "/api/v1/relationships", nil, 200)
	call(t, s, "PUT", "/api/v1/relationships", M{"managers": []M{{"name": "A", "emails": []string{"bad"}}}, "reports": []M{}}, 422)
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"relationships": M{"reports": "bad"}}}, 422)
}

func TestDelegationModelContract(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "out-model", "event_type": "email", "direction": "OUTGOING", "thread_external_id": "thread", "author": "me@example.test", "body": "Иван, подготовь отчёт.", "occurred_at": "2026-09-17T10:00:00Z"}, 202).(map[string]any)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var payload M
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		messages := payload["messages"].([]any)
		text := messages[1].(map[string]any)["content"].(string)
		if !strings.Contains(text, "relationships") || !strings.Contains(text, "existing_delegations") {
			t.Error("missing assignment context")
		}
		candidate := M{"delegation_id": "", "is_new": true, "title": "Подготовить отчёт", "description": "Отчёт", "expected_result": "Отчёт", "assignee_name": "Иван", "assignee_email": "ivan@example.test", "due_at": nil, "evidence": event["body"], "confidence": 0.99, "status": "ASSIGNED"}
		writeJSON(w, 200, M{"message": M{"content": string(must(json.Marshal(M{"items": []M{candidate}})))}})
	}))
	defer upstream.Close()
	call(t, s, "PUT", "/api/v1/admin/settings", M{"settings": M{"llm": M{"base_url": upstream.URL}}}, 200)
	_, err := s.job(context.Background(), func(q *request) bool {
		q.analyzeDelegations(event, M{"direction": "OUTGOING", "body": event["body"], "relationships": M{"reports": []M{{"name": "Иван", "emails": []string{"ivan@example.test"}}}}})
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
	if items := call(t, s, "GET", "/api/v1/delegations", nil, 200).(map[string]any)["items"].([]any); len(items) != 1 {
		t.Fatal(items)
	}
}

func TestDelegationSearchIntent(t *testing.T) {
	for _, text := range []string{"Поручения на проверке", "Что я поручил?", "Просроченные поручения Иванову"} {
		intent := parseSearch(text, time.Now(), false)
		if intent.scope != "delegations" {
			t.Fatalf("%s: %#v", text, intent)
		}
	}
	review := parseSearch("Поручения на проверке", time.Now(), false)
	if len(review.terms) != 0 || len(review.statuses) != 1 || review.statuses[0] != "POSSIBLY_COMPLETED" {
		t.Fatal(review)
	}
}

func TestDelegationEvidenceAndUncertainStatus(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	event := call(t, s, "POST", "/api/v1/events", M{"source_id": "mail", "source_type": "imap", "external_id": "out-evidence", "event_type": "email", "direction": "OUTGOING", "author": "me@example.test", "body": "Иван, подготовь отчёт.", "occurred_at": "2026-09-17T10:00:00Z"}, 202).(map[string]any)
	c := M{"delegation_id": "", "is_new": true, "title": "Отчёт", "assignee_name": "Иван", "assignee_email": "ivan@example.test", "evidence": "Не существующая цитата", "confidence": 0.99}
	_, err := s.job(context.Background(), func(q *request) bool {
		q.applyDelegationAnalysis(event, M{"items": []any{map[string]any(c)}}, nil, str(event, "body"))
		c["evidence"] = event["body"]
		c["confidence"] = 0.3
		q.applyDelegationAnalysis(event, M{"items": []any{map[string]any(c)}}, nil, str(event, "body"))
		if len(q.rows("SELECT id FROM delegations")) != 0 {
			t.Fatal("unproven assignment persisted")
		}
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
}
