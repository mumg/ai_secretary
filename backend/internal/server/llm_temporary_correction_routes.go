package server

func (s *Server) temporaryCorrectionRoutes() {
	s.route("GET /api/v1/admin/llm-temporary-corrections", false, func(q *request) any {
		settings := q.settings()
		q.activeTemporaryCorrections(str(obj(settings, "llm"), "model"), obj(settings, "llm_prompt_corrections"))
		items := q.rows(`SELECT c.correction_id::text,c.report_id::text,c.scope,c.issue_id,c.request_type,c.rule_text,c.model,
			c.template_revision,c.status,c.status_reason,c.fix_id,c.fixed_version,c.applied_at,c.status_changed_at,
			COALESCE(i.release_fix_eligible,false) AS release_fix_eligible
			FROM llm_temporary_corrections c LEFT JOIN llm_system_issues i ON i.issue_id=c.issue_id
			ORDER BY c.applied_at DESC,c.correction_id`)
		history := q.rows(`SELECT correction_id::text,old_status,new_status,reason,fix_id,release_version,changed_at
			FROM llm_temporary_correction_history ORDER BY changed_at DESC,id DESC LIMIT 1000`)
		return M{"items": items, "history": history}
	})
	s.route("POST /api/v1/admin/llm-temporary-corrections/{id}/disable", true, func(q *request) any {
		id := q.id("id")
		row := q.one("SELECT status FROM llm_temporary_corrections WHERE correction_id=$1", id)
		if str(row, "status") == "manual_disabled" {
			return M{"correction_id": id, "status": "manual_disabled"}
		}
		q.exec(`WITH changed AS (
			UPDATE llm_temporary_corrections SET status='manual_disabled',status_reason='Отключено пользователем',
				fix_id='',fixed_version='',status_changed_at=now()
			WHERE correction_id=$1 AND status=$2 RETURNING correction_id
		) INSERT INTO llm_temporary_correction_history(correction_id,old_status,new_status,reason)
		SELECT correction_id,$2,'manual_disabled','Отключено пользователем' FROM changed`, id, str(row, "status"))
		return M{"correction_id": id, "status": "manual_disabled"}
	})
}
