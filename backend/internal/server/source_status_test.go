package server

import (
	"context"
	"testing"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

func TestDeletedSourceStatus(t *testing.T) {
	for _, preconfigured := range []bool{false, true} {
		name := "user"
		if preconfigured {
			name = "preconfigured"
		}
		t.Run(name, func(t *testing.T) {
			s := testServer(t)
			ctx := context.Background()
			if preconfigured {
				s.Config.Preconfiguration = config.Preconfiguration{Sources: []config.SourceDefaults{{ID: "mail", Label: "Mail", SourceType: "imap", Settings: config.Object{}}}}
				if err := s.PreparePreconfiguration(ctx); err != nil {
					t.Fatal(err)
				}
			} else {
				call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
			}
			seed := func() {
				t.Helper()
				_, err := s.job(ctx, func(q *request) bool {
					q.component("source-mail", M{"label": "Mail", "component_type": "event_loader", "status": "ERROR", "metrics": M{}})
					q.component("external-check", M{"label": "External", "component_type": "external", "status": "OK", "metrics": M{}})
					return true
				})
				if err != nil {
					t.Fatal(err)
				}
			}
			check := func(want bool) {
				t.Helper()
				status := call(t, s, "GET", "/api/v1/system/status", nil, 200).(map[string]any)
				found, external := false, false
				for _, v := range status["components"].([]any) {
					c := v.(map[string]any)
					found = found || c["id"] == "source-mail"
					external = external || c["id"] == "external-check"
				}
				if found != want || !external {
					t.Fatalf("source present=%v want=%v; external present=%v", found, want, external)
				}
			}
			seed()
			check(true)
			call(t, s, "DELETE", "/api/v1/admin/sources/mail", nil, 204)
			var count int
			if err := s.Pool.QueryRow(ctx, "SELECT count(*) FROM component_statuses WHERE id='source-mail'").Scan(&count); err != nil {
				t.Fatal(err)
			}
			if count != 0 {
				t.Fatal("deleted source status remains in storage")
			}
			check(false)
			// Old installations and a concurrent worker can leave a late status behind.
			seed()
			check(false)
		})
	}
}
