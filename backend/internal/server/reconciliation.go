package server

import "time"

func (q *request) reconcileFilters() M {
	stats := M{"scanned": 0, "skipped": 0, "ignored": 0, "requeued": 0}
	events := q.rows("SELECT * FROM communication_events WHERE analysis_state<>'PROCESSING' FOR UPDATE")
	stats["scanned"] = len(events)
	threads := map[string]M{}
	for _, event := range events {
		if event["event_type"] == "meeting_invitation" {
			continue
		}
		threads[str(event, "source_id")+"\x00"+str(event, "thread_external_id")] = event
		if filter := q.filtered(event); filter != nil {
			key := "skipped"
			if filter["state"] == "IGNORED" {
				key = "ignored"
			}
			if event["analysis_state"] != filter["state"] {
				stats[key] = stats[key].(int) + 1
			}
			q.update("communication_events", event["id"], M{"analysis_state": filter["state"], "analysis_error": nil, "analysis_model": nil, "analysis_result": M{"skipped": true, "filter": pick(filter, "kind", "value")}, "semantic_summary": event["subject"], "semantic_categories": []string{"Исключено из автоматического анализа"}, "semantic_version": 2, "analyzed_at": time.Now(), "next_analysis_at": nil})
		} else if event["analysis_state"] == "SKIPPED" || event["analysis_state"] == "IGNORED" {
			q.update("communication_events", event["id"], M{"analysis_state": "PENDING", "analysis_error": nil, "analysis_model": nil, "analysis_result": M{}, "analysis_attempts": 0, "next_analysis_at": nil, "semantic_version": 0, "is_mailing": false, "mailing_version": 0})
			stats["requeued"] = stats["requeued"].(int) + 1
		}
	}
	for _, event := range threads {
		q.rebuildThread(event, M{})
	}
	return stats
}
func (q *request) requeueIdentity() int {
	count := 0
	for _, event := range q.rows("SELECT * FROM communication_events WHERE analysis_state='COMPLETED' AND analysis_model IS NOT NULL AND NOT is_mailing FOR UPDATE SKIP LOCKED") {
		if boolean(obj(obj(event, "analysis_result"), "assignment_signals"), "eligible") {
			continue
		}
		event["body"] = cleanEmail(str(event, "body"))
		if !boolean(q.assignment(event), "eligible") {
			continue
		}
		q.update("communication_events", event["id"], M{"analysis_state": "PENDING", "analysis_error": nil, "next_analysis_at": nil, "analysis_attempts": 0})
		count++
	}
	return count
}
