package server

import (
	"encoding/json"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/mumg/ai_secretary/backend/internal/config"
	"github.com/mumg/ai_secretary/backend/internal/gatewaylink"
)

var recommendationUUID = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

func validSupportCorrection(c M) bool {
	a := obj(c, "applicability")
	id, scope, issue := str(c, "correction_id"), str(c, "scope"), str(c, "issue_id")
	if !recommendationUUID.MatchString(id) || (scope != "individual" && scope != "systemic") || (scope == "individual" && issue != "") || (scope == "systemic" && !recommendationUUID.MatchString(issue)) {
		return false
	}
	if int(num(c, "revision")) < 1 || a["component"] != "backend" || str(a, "template_revision") == "" || str(a, "versions") == "" || len(str(a, "model")) > 200 {
		return false
	}
	_, known := config.Section(config.Config{}.Defaults(), "llm_prompt_corrections")[str(c, "request_type")]
	text := str(c, "text")
	return known && utf8.ValidString(text) && strings.TrimSpace(text) != "" && utf8.RuneCountInString(text) <= 4000 && str(c, "reason") != "" && str(c, "expected") != "" && str(c, "check") != ""
}

// A user has already approved the rule text. Reclassifying that same rule as a
// systemic fix link does not change the prompt and does not need new consent.
func sameApprovedRule(old, next M) bool {
	if str(old, "scope") != "individual" || str(next, "scope") != "systemic" ||
		str(old, "request_type") != str(next, "request_type") || str(old, "text") != str(next, "text") {
		return false
	}
	before, after := obj(old, "applicability"), obj(next, "applicability")
	for _, key := range []string{"component", "versions", "template_revision", "model"} {
		if str(before, key) != str(after, key) {
			return false
		}
	}
	return true
}

// Only the mTLS gateway's public reply events may create an offer. Candidate
// previews have proposed_correction but no correction object and are ignored.
func (q *request) receiveDiagnosticRecommendations(reportID string, status gatewaylink.DiagnosticStatus) {
	for _, message := range status.Messages {
		if !message.Public || message.Kind != "reply" || message.ID < 1 {
			continue
		}
		var content struct {
			Correction     M      `json:"correction"`
			IssueID        string `json:"issue_id"`
			ProposalStatus string `json:"proposal_status"`
		}
		if json.Unmarshal(message.Content, &content) != nil {
			continue
		}
		if content.ProposalStatus == "pending_review" && recommendationUUID.MatchString(content.IssueID) {
			q.exec(`UPDATE llm_temporary_corrections SET status='needs_review',status_reason='Позднее обращение связано с системной проблемой',status_changed_at=now()
				WHERE report_id=$1 AND scope='individual' AND status='active'`, reportID)
			q.exec(`UPDATE diagnostic_recommendations SET status='superseded',decided_at=now() WHERE report_id=$1 AND status IN ('offered','applied') AND payload->>'scope'='individual'`, reportID)
			continue
		}
		if !validSupportCorrection(content.Correction) {
			continue
		}
		id := str(content.Correction, "correction_id")
		revision := int(num(content.Correction, "revision"))
		previous := q.rows(`SELECT revision,status,payload FROM diagnostic_recommendations WHERE correction_id=$1`, id)
		autoApplied := false
		if len(previous) > 0 && int(num(previous[0], "revision")) < revision && str(previous[0], "status") == "applied" {
			if sameApprovedRule(obj(previous[0], "payload"), content.Correction) {
				applied := q.rows(`SELECT scope,status,rule_text,request_type,recommendation_revision FROM llm_temporary_corrections WHERE correction_id=$1 FOR UPDATE`, id)
				if len(applied) == 1 && str(applied[0], "scope") == "individual" && str(applied[0], "status") == "active" &&
					str(applied[0], "rule_text") == str(content.Correction, "text") && str(applied[0], "request_type") == str(content.Correction, "request_type") &&
					int(num(applied[0], "recommendation_revision")) == int(num(previous[0], "revision")) {
					issueID := str(content.Correction, "issue_id")
					q.exec(`INSERT INTO llm_system_issues(issue_id,title) VALUES($1,$2) ON CONFLICT(issue_id) DO NOTHING`, issueID, str(content.Correction, "reason"))
					issue := q.one(`SELECT release_fix_eligible FROM llm_system_issues WHERE issue_id=$1`, issueID)
					if boolean(issue, "release_fix_eligible") {
						q.exec(`UPDATE llm_temporary_corrections SET scope='systemic',issue_id=$2,recommendation_revision=$3,status_changed_at=now()
							WHERE correction_id=$1 AND scope='individual' AND status='active'`, id, issueID, revision)
						autoApplied = true
					}
				}
			}
			if !autoApplied {
				q.exec(`UPDATE llm_temporary_corrections SET status='needs_review',status_reason='Поддержка обновила рекомендацию',status_changed_at=now() WHERE correction_id=$1 AND status='active'`, id)
			}
		}
		if str(content.Correction, "scope") == "systemic" {
			q.exec(`UPDATE llm_temporary_corrections SET status='needs_review',status_reason='Предложена системная коррекция',status_changed_at=now()
				WHERE report_id=$1 AND request_type=$2 AND correction_id<>$3 AND scope='individual' AND status='active'`, reportID, str(content.Correction, "request_type"), id)
			q.exec(`UPDATE diagnostic_recommendations SET status='superseded',decided_at=now()
				WHERE report_id=$1 AND correction_id<>$2 AND status IN ('offered','applied') AND payload->>'scope'='individual' AND payload->>'request_type'=$3`, reportID, id, str(content.Correction, "request_type"))
		}
		encoded, _ := json.Marshal(content.Correction)
		offerStatus := "offered"
		if autoApplied {
			offerStatus = "applied"
		}
		q.exec(`INSERT INTO diagnostic_recommendations(correction_id,report_id,revision,event_id,payload,status,decided_at)
			VALUES($1,$2,$3,$4,$5::jsonb,$6,CASE WHEN $6='applied' THEN now() ELSE NULL END) ON CONFLICT(correction_id) DO UPDATE SET
			revision=EXCLUDED.revision,event_id=EXCLUDED.event_id,payload=EXCLUDED.payload,status=EXCLUDED.status,decided_at=EXCLUDED.decided_at
			WHERE diagnostic_recommendations.report_id=EXCLUDED.report_id AND diagnostic_recommendations.revision<EXCLUDED.revision`, id, reportID, revision, message.ID, string(encoded), offerStatus)
	}
}

func (q *request) diagnosticOffers(reportID string) []M {
	return q.rows(`SELECT correction_id::text,revision,payload,status,received_at,decided_at FROM diagnostic_recommendations WHERE report_id=$1 ORDER BY received_at DESC`, reportID)
}

func (s *Server) diagnosticRecommendationRoutes() {
	s.route("POST /api/v1/diagnostic-reports/{id}/corrections/{correction_id}/apply", true, func(q *request) any {
		reportID, correctionID := q.id("id"), q.id("correction_id")
		offer := q.one(`SELECT payload,status FROM diagnostic_recommendations WHERE report_id=$1 AND correction_id=$2`, reportID, correctionID)
		if str(offer, "status") == "applied" {
			return M{"correction_id": correctionID, "status": "applied"}
		}
		if str(offer, "status") != "offered" {
			fail(409, "Предложение уже отклонено")
		}
		c := obj(offer, "payload")
		if !validSupportCorrection(c) || str(c, "correction_id") != correctionID {
			fail(422, "Некорректная рекомендация")
		}
		applicability := obj(c, "applicability")
		settings := q.settings()
		model := str(obj(settings, "llm"), "model")
		if str(applicability, "template_revision") != llmPromptTemplateRevision || (str(applicability, "model") != "" && str(applicability, "model") != model) {
			fail(409, "Модель или шаблон запроса изменились; нужна новая рекомендация")
		}
		if existing := q.rows(`SELECT correction_id::text FROM llm_temporary_corrections WHERE request_type=$1 AND status='active' AND correction_id<>$2`, str(c, "request_type"), correctionID); len(existing) != 0 {
			fail(409, "Для этого типа запроса уже действует временная коррекция")
		}
		issueID := str(c, "issue_id")
		if str(c, "scope") == "systemic" {
			q.exec(`INSERT INTO llm_system_issues(issue_id,title) VALUES($1,$2) ON CONFLICT(issue_id) DO NOTHING`, issueID, str(c, "reason"))
			issue := q.one(`SELECT release_fix_eligible FROM llm_system_issues WHERE issue_id=$1`, issueID)
			if !boolean(issue, "release_fix_eligible") {
				fail(409, "Системная проблема отозвана")
			}
		}
		personal := str(obj(settings, "llm_prompt_corrections"), str(c, "request_type"))
		manifest, err := parseLLMReleaseManifest(llmFixManifestJSON, q.server.Version)
		if err != nil {
			fail(409, "Манифест установленного релиза недоступен")
		}
		probe := M{"scope": c["scope"], "issue_id": issueID, "release_fix_eligible": str(c, "scope") == "systemic", "request_type": c["request_type"], "rule_text": c["text"], "model": applicability["model"], "template_revision": applicability["template_revision"], "personal_correction_hash": correctionHash(personal), "status": "active"}
		status, _, _, _ := correctionTransition(probe, manifest, model, personal)
		if status != "active" {
			fail(409, "Коррекция уже исправлена релизом или неприменима")
		}
		existing := q.rows(`SELECT report_id::text,recommendation_revision,status FROM llm_temporary_corrections WHERE correction_id=$1`, correctionID)
		if len(existing) != 0 && (str(existing[0], "report_id") != reportID || int(num(existing[0], "recommendation_revision")) >= int(num(c, "revision"))) {
			fail(409, "Эта версия коррекции уже была рассмотрена")
		}
		q.exec(`INSERT INTO llm_temporary_corrections(correction_id,report_id,scope,issue_id,request_type,rule_text,model,template_revision,personal_correction_hash,status,recommendation_revision)
			VALUES($1,$2,$3,NULLIF($4,''),$5,$6,$7,$8,$9,'active',$10)
			ON CONFLICT(correction_id) DO UPDATE SET scope=EXCLUDED.scope,issue_id=EXCLUDED.issue_id,request_type=EXCLUDED.request_type,
				rule_text=EXCLUDED.rule_text,model=EXCLUDED.model,template_revision=EXCLUDED.template_revision,
				personal_correction_hash=EXCLUDED.personal_correction_hash,status='active',status_reason='',fix_id='',fixed_version='',
				recommendation_revision=EXCLUDED.recommendation_revision,status_changed_at=now()
			WHERE llm_temporary_corrections.report_id=EXCLUDED.report_id AND llm_temporary_corrections.recommendation_revision<EXCLUDED.recommendation_revision`, correctionID, reportID, str(c, "scope"), issueID, str(c, "request_type"), str(c, "text"), str(applicability, "model"), str(applicability, "template_revision"), correctionHash(personal), int(num(c, "revision")))
		q.exec(`UPDATE diagnostic_recommendations SET status='applied',decided_at=now() WHERE correction_id=$1 AND status='offered'`, correctionID)
		return M{"correction_id": correctionID, "status": "applied"}
	})
	s.route("POST /api/v1/diagnostic-reports/{id}/corrections/{correction_id}/decline", true, func(q *request) any {
		reportID, correctionID := q.id("id"), q.id("correction_id")
		q.exec(`UPDATE diagnostic_recommendations SET status='declined',decided_at=now() WHERE report_id=$1 AND correction_id=$2 AND status='offered'`, reportID, correctionID)
		return M{"correction_id": correctionID, "status": "declined"}
	})
}
