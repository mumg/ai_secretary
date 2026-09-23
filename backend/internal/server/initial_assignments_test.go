package server

import (
	"fmt"
	"testing"
	"time"
)

func TestInitialAssignmentWindowCreatesTasksAndDelegations(t *testing.T) {
	s := testServer(t)
	created := time.Now().Add(-90 * 24 * time.Hour).UTC().Truncate(time.Second)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "limited", "label": "Limited", "source_type": "imap", "enabled": false, "settings": M{"initial_assignment_days": 7}}, 201)
	policySQL(t, s, "UPDATE communication_sources SET created_at=$1 WHERE id='limited'", created)
	for i, tc := range []struct {
		at      time.Time
		allowed bool
	}{{created.Add(-8 * 24 * time.Hour), false}, {created.Add(-7 * 24 * time.Hour), true}, {created.Add(-time.Hour), true}, {created.Add(time.Hour), true}} {
		t.Run(fmt.Sprint(i), func(t *testing.T) {
			beforeTasks, beforeDelegations := policyCount(t, s, "tasks"), policyCount(t, s, "delegations")
			policyJob(t, s, func(q *request) {
				event := q.insert("communication_events", M{"source_id": "limited", "source_type": "imap", "external_id": fmt.Sprint(i), "event_type": "email", "direction": "INCOMING", "thread_external_id": fmt.Sprint(i), "body": "Подготовь отчёт", "occurred_at": tc.at, "content_hash": fmt.Sprint(i)})
				if q.initialAssignmentAllowed(event) != tc.allowed {
					t.Fatal("wrong fixed window")
				}
				candidate := M{"title": "Отчёт", "priority": "NORMAL", "assignee": "user", "assignee_address": "me@example.test", "confidence": 0.99, "evidence": "Подготовь отчёт", "assignment_evidence": "Подготовь отчёт"}
				q.taskCandidate(event, candidate, M{"eligible": true}, []string{"me@example.test"}, nil, obj(q.settings(), "llm"))
				event["direction"] = "OUTGOING"
				c := M{"delegation_id": "", "is_new": true, "title": "Поручение", "assignee_name": "Иван", "assignee_email": "ivan@example.test", "confidence": 0.99, "evidence": "Подготовь отчёт", "status": "ASSIGNED"}
				q.applyDelegationAnalysis(event, M{"items": []any{map[string]any(c)}}, nil, str(event, "body"))
			})
			want := 0
			if tc.allowed {
				want = 1
			}
			if policyCount(t, s, "tasks")-beforeTasks != want || policyCount(t, s, "delegations")-beforeDelegations != want {
				t.Fatal("assignment creation ignored depth")
			}
		})
	}
	if policyCount(t, s, "communication_events") != 4 {
		t.Fatal("archive lost old events")
	}
	// Completing initial sync does not permit old assignments on a later replay.
	policySQL(t, s, "UPDATE communication_sources SET last_sync_at=now() WHERE id='limited'")
	policyJob(t, s, func(q *request) {
		if q.initialAssignmentAllowed(M{"source_id": "limited", "occurred_at": created.Add(-8 * 24 * time.Hour).Format(time.RFC3339)}) {
			t.Fatal("window changed after initialization")
		}
	})
}

func TestInitialAssignmentZeroAndLegacy(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "zero", "label": "Zero", "source_type": "imap", "enabled": false, "settings": M{"initial_assignment_days": 0}}, 201)
	policyJob(t, s, func(q *request) {
		source := q.get("communication_sources", "zero")
		at := *timestamp(source["created_at"])
		for _, test := range []struct {
			delta   time.Duration
			allowed bool
		}{{-time.Second, false}, {0, false}, {time.Second, true}} {
			if q.initialAssignmentAllowed(M{"source_id": "zero", "occurred_at": at.Add(test.delta).Format(time.RFC3339Nano)}) != test.allowed {
				t.Fatal(test)
			}
		}
		q.insert("communication_sources", M{"id": "legacy", "label": "Legacy", "source_type": "imap", "enabled": false})
		if !q.initialAssignmentAllowed(M{"source_id": "legacy", "occurred_at": at.AddDate(-2, 0, 0).Format(time.RFC3339)}) {
			t.Fatal("legacy source changed")
		}
	})
}

func TestInitialAssignmentSettingValidationAndPreservation(t *testing.T) {
	s := testServer(t)
	source := M{"id": "source", "label": "Source", "source_type": "imap", "enabled": false, "settings": M{}}
	row := call(t, s, "POST", "/api/v1/admin/sources", source, 201).(map[string]any)
	if num(obj(row, "settings"), "initial_assignment_days") != 30 {
		t.Fatal(row)
	}
	for _, bad := range []any{-1, 366, 1.5, "7", true, nil} {
		source["settings"] = M{"initial_assignment_days": bad}
		call(t, s, "PUT", "/api/v1/admin/sources/source", source, 422)
	}
	policyJob(t, s, func(q *request) {
		q.insert("source_cursors", M{"source_id": "source", "cursor_key": "mail", "cursor_value": "123"})
	})
	source["settings"] = M{"initial_assignment_days": 0}
	call(t, s, "PUT", "/api/v1/admin/sources/source", source, 200)
	source["settings"] = M{}
	row = call(t, s, "PUT", "/api/v1/admin/sources/source", source, 200).(map[string]any)
	if value, ok := obj(row, "settings")["initial_assignment_days"]; !ok || value != float64(0) {
		t.Fatal("old client erased saved depth")
	}
	if policyCount(t, s, "source_cursors") != 1 {
		t.Fatal("depth edit reset import cursor")
	}
}
