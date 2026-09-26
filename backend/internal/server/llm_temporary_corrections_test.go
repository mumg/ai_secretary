package server

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/mumg/ai_secretary/backend/internal/store"
)

func TestReleaseFixDisablesOnlyMatchingTemporaryCorrection(t *testing.T) {
	manifest := llmReleaseManifest{ReleaseVersion: "0.7.52", Fixes: []llmReleaseFix{{FixID: "fix-task-status", IssueID: "issue-status", RequestType: "task_extraction", TemplateRevision: llmPromptTemplateRevision, Model: "qwen"}}}
	row := M{"scope": "systemic", "issue_id": "issue-status", "release_fix_eligible": true, "request_type": "task_extraction", "rule_text": "Не создавать задачу из статуса", "model": "qwen", "template_revision": llmPromptTemplateRevision, "personal_correction_hash": correctionHash(""), "status": "active"}
	status, reason, fixID, version := correctionTransition(row, manifest, "qwen", "")
	if status != "disabled_by_fix" || reason != "Исправлено в версии 0.7.52" || fixID != "fix-task-status" || version != "0.7.52" {
		t.Fatalf("matching fix did not disable the temporary correction: %s %s %s %s", status, reason, fixID, version)
	}
	if status, _, _, _ := correctionTransition(row, manifest, "other-model", ""); status != "needs_review" {
		t.Fatal("correction for another model was treated as fixed")
	}
	row["scope"] = "individual"
	if status, _, _, _ := correctionTransition(row, manifest, "qwen", ""); status != "active" {
		t.Fatal("individual correction was disabled by a release fix")
	}
	row["scope"] = "systemic"
	row["release_fix_eligible"] = false
	if status, _, _, _ := correctionTransition(row, manifest, "qwen", ""); status != "active" {
		t.Fatal("issue outside the accepted backlog was treated as fixed")
	}
	row["release_fix_eligible"] = true
	row["status"] = "manual_disabled"
	if status, _, _, _ := correctionTransition(row, manifest, "qwen", ""); status != "manual_disabled" {
		t.Fatal("manual disable was overridden by the release")
	}
	row["status"] = "disabled_by_fix"
	if status, _, _, _ := correctionTransition(row, llmReleaseManifest{ReleaseVersion: "0.7.51"}, "qwen", ""); status != "active" {
		t.Fatal("rollback did not restore an applicable correction")
	}
	row["personal_correction_hash"] = correctionHash("old personal instruction")
	if status, _, _, _ := correctionTransition(row, llmReleaseManifest{}, "qwen", "changed personal instruction"); status != "needs_review" {
		t.Fatal("changed personal instructions did not require review")
	}
}

func TestLLMFixManifestMustMatchInstalledRelease(t *testing.T) {
	data, err := json.Marshal(llmReleaseManifest{SchemaVersion: 1, Component: "backend", ReleaseVersion: "0.7.52", Fixes: []llmReleaseFix{{FixID: "fix-1", IssueID: "issue-1", RequestType: "task_extraction", TemplateRevision: llmPromptTemplateRevision}}})
	if err != nil {
		t.Fatal(err)
	}
	if _, err = parseLLMReleaseManifest(data, "0.7.51"); err == nil {
		t.Fatal("manifest from another release was accepted")
	}
	if manifest, err := parseLLMReleaseManifest(data, "0.7.52"); err != nil || len(manifest.Fixes) != 1 {
		t.Fatalf("valid installed manifest rejected: %+v %v", manifest, err)
	}
}

func TestBundledLLMFixManifestMatchesVersion(t *testing.T) {
	version, err := os.ReadFile("../../../version")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := parseLLMReleaseManifest(llmFixManifestJSON, strings.TrimSpace(string(version))); err != nil {
		t.Fatal(err)
	}
}

func TestInstalledFixReconciliationIsIdempotentAndRollbackRestores(t *testing.T) {
	s := testServer(t)
	s.Version = "0.7.51"
	original := llmFixManifestJSON
	defer func() { llmFixManifestJSON = original }()
	llmFixManifestJSON = must(json.Marshal(llmReleaseManifest{SchemaVersion: 1, Component: "backend", ReleaseVersion: "0.7.51", Fixes: []llmReleaseFix{{FixID: "fix-1", IssueID: "issue-1", RequestType: "task_extraction", TemplateRevision: llmPromptTemplateRevision}}}))
	id := store.UUID()
	individualID := store.UUID()
	ctx := context.Background()
	_, err := s.Pool.Exec(ctx, `INSERT INTO llm_system_issues(issue_id,title) VALUES('issue-1','Статус ошибочно создаёт задачу')`)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Pool.Exec(ctx, `INSERT INTO llm_temporary_corrections(correction_id,scope,issue_id,request_type,rule_text,template_revision,status)
		VALUES($1,'systemic','issue-1','task_extraction','Не создавай задачу из статуса',$2,'active')`, id, llmPromptTemplateRevision)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Pool.Exec(ctx, `INSERT INTO llm_temporary_corrections(correction_id,scope,request_type,rule_text,template_revision,status)
		VALUES($1,'individual','archive_answer','Отвечай подробнее',$2,'active')`, individualID, llmPromptTemplateRevision)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Pool.Exec(ctx, `INSERT INTO llm_temporary_corrections(correction_id,scope,issue_id,request_type,rule_text,template_revision,status)
		VALUES($1,'individual','issue-1','archive_answer','Недопустимо',$2,'active')`, store.UUID(), llmPromptTemplateRevision)
	if err == nil {
		t.Fatal("individual correction accepted a release issue ID")
	}
	_, err = s.Pool.Exec(ctx, `INSERT INTO llm_temporary_corrections(correction_id,scope,issue_id,request_type,rule_text,template_revision,status)
		VALUES($1,'systemic','unknown-issue','archive_answer','Недопустимо',$2,'active')`, store.UUID(), llmPromptTemplateRevision)
	if err == nil {
		t.Fatal("unrecognized systemic issue was accepted")
	}
	for range 2 {
		if err = s.ReconcileTemporaryCorrections(ctx); err != nil {
			t.Fatal(err)
		}
	}
	var status, fixedVersion string
	if err = s.Pool.QueryRow(ctx, "SELECT status,fixed_version FROM llm_temporary_corrections WHERE correction_id=$1", id).Scan(&status, &fixedVersion); err != nil || status != "disabled_by_fix" || fixedVersion != "0.7.51" {
		t.Fatal(status, fixedVersion, err)
	}
	if err = s.Pool.QueryRow(ctx, "SELECT status FROM llm_temporary_corrections WHERE correction_id=$1", individualID).Scan(&status); err != nil || status != "active" {
		t.Fatal("individual correction changed after release fix", status, err)
	}
	var transitions int
	if err = s.Pool.QueryRow(ctx, "SELECT count(*) FROM llm_temporary_correction_history WHERE correction_id=$1", id).Scan(&transitions); err != nil || transitions != 1 {
		t.Fatal(transitions, err)
	}
	if _, err = s.Pool.Exec(ctx, "UPDATE llm_system_issues SET release_fix_eligible=false WHERE issue_id='issue-1'"); err != nil {
		t.Fatal(err)
	}
	if err = s.ReconcileTemporaryCorrections(ctx); err != nil {
		t.Fatal(err)
	}
	if err = s.Pool.QueryRow(ctx, "SELECT status FROM llm_temporary_corrections WHERE correction_id=$1", id).Scan(&status); err != nil || status != "active" {
		t.Fatal("withdrawn backlog issue remained fixed", status, err)
	}
	if _, err = s.Pool.Exec(ctx, "UPDATE llm_system_issues SET release_fix_eligible=true WHERE issue_id='issue-1'"); err != nil {
		t.Fatal(err)
	}
	if err = s.ReconcileTemporaryCorrections(ctx); err != nil {
		t.Fatal(err)
	}
	s.Version = "0.7.50"
	if err = s.ReconcileTemporaryCorrections(ctx); err != nil {
		t.Fatal(err)
	}
	if err = s.Pool.QueryRow(ctx, "SELECT status FROM llm_temporary_corrections WHERE correction_id=$1", id).Scan(&status); err != nil || status != "active" {
		t.Fatal(status, err)
	}
	call(t, s, "POST", "/api/v1/admin/llm-temporary-corrections/"+id+"/disable", M{}, 200)
	s.Version = "0.7.51"
	if err = s.ReconcileTemporaryCorrections(ctx); err != nil {
		t.Fatal(err)
	}
	if err = s.Pool.QueryRow(ctx, "SELECT status FROM llm_temporary_corrections WHERE correction_id=$1", id).Scan(&status); err != nil || status != "manual_disabled" {
		t.Fatal(status, err)
	}
}
