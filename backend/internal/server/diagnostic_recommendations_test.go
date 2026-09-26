package server

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/mumg/ai_secretary/backend/internal/gatewaylink"
	"github.com/mumg/ai_secretary/backend/internal/store"
)

func TestSupportCorrectionRequiresOwnerDecisionAndRetiresWithReleaseFix(t *testing.T) {
	s := testServer(t)
	s.Version = strings.TrimSpace(string(must(os.ReadFile("../../../version"))))
	reportID, correctionID, issueID, individualID := store.UUID(), store.UUID(), store.UUID(), store.UUID()
	_, err := s.Pool.Exec(context.Background(), `INSERT INTO diagnostic_reports(report_id,payload_cipher,payload_sha256,state) VALUES($1,'cipher',$2,'in_review')`, reportID, "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	correction := M{"correction_id": correctionID, "revision": 1, "scope": "systemic", "issue_id": issueID, "request_type": "task_extraction", "text": "Не назначать задачу без исполнителя", "reason": "Ложное назначение", "expected": "Нет задачи", "check": "Письмо без исполнителя", "applicability": M{"component": "backend", "versions": "0.7.53", "template_revision": llmPromptTemplateRevision}}
	content, _ := json.Marshal(M{"correction": correction})
	individual := M{"correction_id": individualID, "revision": 1, "scope": "individual", "request_type": "task_extraction", "text": "Старое персональное правило", "reason": "Старый ответ", "expected": "Нет задачи", "check": "Письмо", "applicability": M{"component": "backend", "versions": "0.7.53", "template_revision": llmPromptTemplateRevision}}
	individualContent, _ := json.Marshal(M{"correction": individual})
	preview, _ := json.Marshal(M{"issue_id": issueID, "proposed_correction": "Только кандидат", "proposal_status": "pending_review"})
	status := gatewaylink.DiagnosticStatus{Messages: []gatewaylink.DiagnosticMessage{{ID: 1, Kind: "reply", Public: true, Content: individualContent}, {ID: 2, Kind: "reply", Public: true, Content: preview}, {ID: 3, Kind: "reply", Public: true, Content: content}}}
	for range 2 {
		var tx pgx.Tx
		tx, err = s.Pool.Begin(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		q := &request{Context: context.Background(), db: tx, server: s}
		func() {
			defer func() {
				if recovered := recover(); recovered != nil {
					_ = tx.Rollback(context.Background())
					t.Fatalf("receive recommendation: %v", recovered)
				}
			}()
			q.receiveDiagnosticRecommendations(reportID, status)
		}()
		if err = tx.Commit(context.Background()); err != nil {
			t.Fatal(err)
		}
	}
	var offers, applied int
	if err = s.Pool.QueryRow(context.Background(), `SELECT count(*) FROM diagnostic_recommendations WHERE report_id=$1`, reportID).Scan(&offers); err != nil || offers != 2 {
		t.Fatal("offers", offers, err)
	}
	var superseded string
	if err = s.Pool.QueryRow(context.Background(), `SELECT status FROM diagnostic_recommendations WHERE correction_id=$1`, individualID).Scan(&superseded); err != nil || superseded != "superseded" {
		t.Fatal("old individual proposal remained applicable", superseded, err)
	}
	if err = s.Pool.QueryRow(context.Background(), `SELECT count(*) FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&applied); err != nil || applied != 0 {
		t.Fatal("offer changed the prompt without consent", applied, err)
	}
	call(t, s, "POST", "/api/v1/diagnostic-reports/"+reportID+"/corrections/"+correctionID+"/apply", M{}, 200)
	call(t, s, "POST", "/api/v1/diagnostic-reports/"+reportID+"/corrections/"+correctionID+"/apply", M{}, 200)
	var storedIssue, storedStatus string
	if err = s.Pool.QueryRow(context.Background(), `SELECT issue_id,status FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&storedIssue, &storedStatus); err != nil || storedIssue != issueID || storedStatus != "active" {
		t.Fatal(storedIssue, storedStatus, err)
	}
	correction["revision"] = 2
	correction["text"] = "Уточнённое правило без назначения"
	updated, _ := json.Marshal(M{"correction": correction})
	status.Messages = append(status.Messages, gatewaylink.DiagnosticMessage{ID: 4, Kind: "reply", Public: true, Content: updated})
	_, err = s.job(context.Background(), func(q *request) bool { q.receiveDiagnosticRecommendations(reportID, status); return true })
	if err != nil {
		t.Fatal(err)
	}
	if err = s.Pool.QueryRow(context.Background(), `SELECT status FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&storedStatus); err != nil || storedStatus != "needs_review" {
		t.Fatal("new revision did not suspend old text", storedStatus, err)
	}
	call(t, s, "POST", "/api/v1/diagnostic-reports/"+reportID+"/corrections/"+correctionID+"/apply", M{}, 200)
	var installedRevision int
	if err = s.Pool.QueryRow(context.Background(), `SELECT recommendation_revision,status FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&installedRevision, &storedStatus); err != nil || installedRevision != 2 || storedStatus != "active" {
		t.Fatal("new revision was not explicitly applied", installedRevision, storedStatus, err)
	}
	original := llmFixManifestJSON
	defer func() { llmFixManifestJSON = original }()
	llmFixManifestJSON, _ = json.Marshal(llmReleaseManifest{SchemaVersion: 1, Component: "backend", ReleaseVersion: s.Version, Fixes: []llmReleaseFix{{FixID: "verified-fix", IssueID: issueID, RequestType: "task_extraction", TemplateRevision: llmPromptTemplateRevision, FixedVersion: s.Version}}})
	if err = s.ReconcileTemporaryCorrections(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err = s.Pool.QueryRow(context.Background(), `SELECT status FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&storedStatus); err != nil || storedStatus != "disabled_by_fix" {
		t.Fatal("installed fix did not retire recommendation", storedStatus, err)
	}
}

func TestApprovedRuleKeepsConsentWhenLinkedToSystemicIssue(t *testing.T) {
	s := testServer(t)
	s.Version = strings.TrimSpace(string(must(os.ReadFile("../../../version"))))
	reportID, correctionID, issueID := store.UUID(), store.UUID(), store.UUID()
	ctx := context.Background()
	if _, err := s.Pool.Exec(ctx, `INSERT INTO diagnostic_reports(report_id,payload_cipher,payload_sha256,state) VALUES($1,'cipher',$2,'in_review')`, reportID, "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"); err != nil {
		t.Fatal(err)
	}
	correction := M{"correction_id": correctionID, "revision": 1, "scope": "individual", "request_type": "task_extraction", "text": "Не назначать задачу пользователю только из-за копии письма", "reason": "Ложное назначение", "expected": "Нет задачи", "check": "Письмо без поручения", "applicability": M{"component": "backend", "versions": "0.7.53", "template_revision": llmPromptTemplateRevision}}
	first, _ := json.Marshal(M{"correction": correction})
	status := gatewaylink.DiagnosticStatus{Messages: []gatewaylink.DiagnosticMessage{{ID: 1, Kind: "reply", Public: true, Content: first}}}
	if _, err := s.job(ctx, func(q *request) bool { q.receiveDiagnosticRecommendations(reportID, status); return true }); err != nil {
		t.Fatal(err)
	}
	call(t, s, "POST", "/api/v1/diagnostic-reports/"+reportID+"/corrections/"+correctionID+"/apply", M{}, 200)
	correction["revision"], correction["scope"], correction["issue_id"] = 2, "systemic", issueID
	second, _ := json.Marshal(M{"correction": correction})
	status.Messages = append(status.Messages, gatewaylink.DiagnosticMessage{ID: 2, Kind: "reply", Public: true, Content: second})
	for range 2 {
		if _, err := s.job(ctx, func(q *request) bool { q.receiveDiagnosticRecommendations(reportID, status); return true }); err != nil {
			t.Fatal(err)
		}
	}
	var scope, storedIssue, activeStatus, offerStatus string
	var revision int
	if err := s.Pool.QueryRow(ctx, `SELECT scope,issue_id,status,recommendation_revision FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&scope, &storedIssue, &activeStatus, &revision); err != nil || scope != "systemic" || storedIssue != issueID || activeStatus != "active" || revision != 2 {
		t.Fatal("approved rule was not linked to systemic issue", scope, storedIssue, activeStatus, revision, err)
	}
	if err := s.Pool.QueryRow(ctx, `SELECT status FROM diagnostic_recommendations WHERE correction_id=$1`, correctionID).Scan(&offerStatus); err != nil || offerStatus != "applied" {
		t.Fatal("same rule asked for consent again", offerStatus, err)
	}
	correction["revision"], correction["text"] = 3, "Другое правило для поручений"
	third, _ := json.Marshal(M{"correction": correction})
	status.Messages = append(status.Messages, gatewaylink.DiagnosticMessage{ID: 3, Kind: "reply", Public: true, Content: third})
	if _, err := s.job(ctx, func(q *request) bool { q.receiveDiagnosticRecommendations(reportID, status); return true }); err != nil {
		t.Fatal(err)
	}
	if err := s.Pool.QueryRow(ctx, `SELECT status FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID).Scan(&activeStatus); err != nil || activeStatus != "needs_review" {
		t.Fatal("changed rule did not require consent", activeStatus, err)
	}
}
