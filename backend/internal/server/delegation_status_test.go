package server

import "testing"

func TestStatusOnlyOutgoingMail(t *testing.T) {
	for _, test := range []struct {
		body   string
		status bool
	}{
		{"На данный момент задачи следующие:\n* Собрать перечень методов\n* Реализовать логирование", true},
		{"FYI\n\nС уважением,\nМаксим\nОт: Иван\nПрошу подготовить отчёт", true},
		{"На данный момент задачи следующие:\nИван, пожалуйста, собери перечень методов", false},
		{"Иван, подготовь отчёт о поставках", false},
	} {
		if got := statusOnlyOutgoingMail(test.body); got != test.status {
			t.Errorf("statusOnlyOutgoingMail(%q) = %v, want %v", test.body, got, test.status)
		}
	}
}

func TestStatusMailDoesNotCreateDelegation(t *testing.T) {
	s := testServer(t)
	body := "На данный момент задачи следующие:\n* Собрать перечень методов\n* Реализовать логирование"
	event := M{"event_type": "email", "direction": "OUTGOING", "body": body,
		"source_id": "mail", "occurred_at": "2026-09-24T10:00:00Z"}
	policyJob(t, s, func(q *request) {
		candidate := M{"delegation_id": "", "is_new": true, "title": "Собрать перечень методов",
			"assignee_name": "Иван", "assignee_email": "ivan@example.test", "evidence": "Собрать перечень методов",
			"confidence": 0.99, "status": "ASSIGNED"}
		q.applyDelegationAnalysis(event, M{"items": []any{map[string]any(candidate)}}, nil, body)
	})
	if got := policyCount(t, s, "delegations"); got != 0 {
		t.Fatalf("status email created %d delegations", got)
	}
}
