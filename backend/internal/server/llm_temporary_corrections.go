package server

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"strings"
	"unicode/utf8"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

// A change to a prompt or its response contract must change this revision.
// Recommendations for another revision are withheld until reviewed again.
const llmPromptTemplateRevision = "2026-09-26"

//go:embed llm_fix_manifest.json
var llmFixManifestJSON []byte

type llmReleaseFix struct {
	FixID            string `json:"fix_id"`
	IssueID          string `json:"issue_id"`
	RequestType      string `json:"request_type"`
	TemplateRevision string `json:"template_revision"`
	Model            string `json:"model,omitempty"`
}

type llmReleaseManifest struct {
	SchemaVersion  int             `json:"schema_version"`
	Component      string          `json:"component"`
	ReleaseVersion string          `json:"release_version"`
	Fixes          []llmReleaseFix `json:"fixes"`
}

func parseLLMReleaseManifest(data []byte, installedVersion string) (llmReleaseManifest, error) {
	var manifest llmReleaseManifest
	if err := json.Unmarshal(data, &manifest); err != nil {
		return manifest, err
	}
	if manifest.SchemaVersion != 1 || manifest.Component != "backend" || manifest.ReleaseVersion != installedVersion || manifest.Fixes == nil {
		return manifest, errors.New("LLM fix manifest does not match the installed backend")
	}
	allowed := config.Section(config.Config{}.Defaults(), "llm_prompt_corrections")
	seen := map[string]bool{}
	seenScope := map[string]bool{}
	for _, fix := range manifest.Fixes {
		if fix.FixID == "" || fix.IssueID == "" || fix.TemplateRevision == "" || len(fix.FixID) > 128 || len(fix.IssueID) > 128 || len(fix.TemplateRevision) > 128 || len(fix.Model) > 200 || seen[fix.FixID] {
			return manifest, errors.New("invalid or duplicated LLM fix identity")
		}
		if _, ok := allowed[fix.RequestType]; !ok {
			return manifest, fmt.Errorf("unknown LLM fix request type %q", fix.RequestType)
		}
		scope := fix.IssueID + "\x00" + fix.RequestType + "\x00" + fix.TemplateRevision + "\x00" + fix.Model
		if seenScope[scope] {
			return manifest, errors.New("duplicated LLM fix scope")
		}
		seen[fix.FixID] = true
		seenScope[scope] = true
	}
	return manifest, nil
}

func correctionHash(text string) string {
	digest := sha256.Sum256([]byte(strings.TrimSpace(text)))
	return hex.EncodeToString(digest[:])
}

func correctionTransition(row M, manifest llmReleaseManifest, model, personal string) (status, reason, fixID, fixedVersion string) {
	if str(row, "status") == "manual_disabled" {
		return "manual_disabled", str(row, "status_reason"), "", ""
	}
	allowed := config.Section(config.Config{}.Defaults(), "llm_prompt_corrections")
	requestType := str(row, "request_type")
	if _, ok := allowed[requestType]; !ok || !utf8.ValidString(str(row, "rule_text")) || strings.TrimSpace(str(row, "rule_text")) == "" || utf8.RuneCountInString(str(row, "rule_text")) > 4000 {
		return "needs_review", "Недопустимый тип или текст коррекции", "", ""
	}
	if str(row, "template_revision") != llmPromptTemplateRevision || (str(row, "model") != "" && str(row, "model") != model) {
		return "needs_review", "Модель или шаблон запроса изменились", "", ""
	}
	approvedPersonalHash := str(row, "personal_correction_hash")
	if approvedPersonalHash != correctionHash(personal) && !(approvedPersonalHash == "" && strings.TrimSpace(personal) == "") {
		return "needs_review", "Постоянные персональные инструкции изменились", "", ""
	}
	// Individual rules have no release issue. Even an accidental matching ID
	// must not turn a personal preference into an automatically retired fix.
	if str(row, "scope") == "systemic" && boolean(row, "release_fix_eligible") && str(row, "issue_id") != "" {
		for _, fix := range manifest.Fixes {
			if fix.IssueID == str(row, "issue_id") && fix.RequestType == requestType && fix.TemplateRevision == llmPromptTemplateRevision && (fix.Model == "" || fix.Model == model) {
				return "disabled_by_fix", "Исправлено в версии " + manifest.ReleaseVersion, fix.FixID, manifest.ReleaseVersion
			}
		}
	}
	if str(row, "status") == "needs_review" && str(row, "status_reason") == "Несколько правил для одного типа запроса" {
		return "needs_review", str(row, "status_reason"), "", ""
	}
	return "active", "", "", ""
}

func (q *request) activeTemporaryCorrections(model string, permanent M) M {
	manifest, err := parseLLMReleaseManifest(llmFixManifestJSON, q.server.Version)
	if err != nil {
		// An absent or mismatched manifest never confirms a release fix.
		slog.Warn("LLM fix manifest unavailable", "error_type", "invalid_manifest")
		manifest = llmReleaseManifest{}
	}
	rows := q.rows(`SELECT c.correction_id::text,c.scope,c.issue_id,c.request_type,c.rule_text,c.model,c.template_revision,
		c.personal_correction_hash,c.status,c.status_reason,c.fix_id,c.fixed_version,
		COALESCE(i.release_fix_eligible,false) AS release_fix_eligible
		FROM llm_temporary_corrections c LEFT JOIN llm_system_issues i ON i.issue_id=c.issue_id
		ORDER BY c.request_type,c.correction_id`)
	for _, row := range rows {
		personal := str(permanent, str(row, "request_type"))
		status, reason, fixID, fixedVersion := correctionTransition(row, manifest, model, personal)
		if status == str(row, "status") && reason == str(row, "status_reason") && fixID == str(row, "fix_id") && fixedVersion == str(row, "fixed_version") {
			continue
		}
		q.exec(`WITH changed AS (
			UPDATE llm_temporary_corrections SET status=$3,status_reason=$4,fix_id=$5,fixed_version=$6,status_changed_at=now()
			WHERE correction_id=$1 AND status=$2 RETURNING correction_id
		) INSERT INTO llm_temporary_correction_history(correction_id,old_status,new_status,reason,fix_id,release_version)
		SELECT correction_id,$2,$3,$4,$5,$6 FROM changed`, str(row, "correction_id"), str(row, "status"), status, reason, fixID, fixedVersion)
	}
	active := q.rows(`SELECT correction_id::text,request_type,rule_text FROM llm_temporary_corrections WHERE status='active' ORDER BY request_type,correction_id`)
	byType := map[string][]M{}
	for _, row := range active {
		byType[str(row, "request_type")] = append(byType[str(row, "request_type")], row)
	}
	result := M{}
	for requestType, group := range byType {
		if len(group) != 1 {
			for _, row := range group {
				q.exec(`WITH changed AS (
					UPDATE llm_temporary_corrections SET status='needs_review',status_reason='Несколько правил для одного типа запроса',status_changed_at=now()
					WHERE correction_id=$1 AND status='active' RETURNING correction_id
				) INSERT INTO llm_temporary_correction_history(correction_id,old_status,new_status,reason)
				SELECT correction_id,'active','needs_review','Несколько правил для одного типа запроса' FROM changed`, str(row, "correction_id"))
			}
			continue
		}
		result[requestType] = str(group[0], "rule_text")
	}
	return result
}

// ReconcileTemporaryCorrections runs before the first request or worker job.
// llmConfig repeats the same idempotent check when model or settings change.
func (s *Server) ReconcileTemporaryCorrections(ctx context.Context) error {
	_, err := s.job(ctx, func(q *request) bool {
		settings := q.settings()
		q.activeTemporaryCorrections(str(obj(settings, "llm"), "model"), obj(settings, "llm_prompt_corrections"))
		return true
	})
	return err
}
