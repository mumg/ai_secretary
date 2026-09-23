package server

import "time"

// The initialization window is anchored to source creation, not the time an
// event happens to be downloaded or analyzed. Missing limits preserve legacy
// sources; API-created sources store an explicit limit.
func (q *request) initialAssignmentAllowed(event M) bool {
	sources := q.rows("SELECT created_at,settings FROM communication_sources WHERE id=$1", event["source_id"])
	if len(sources) == 0 {
		return true
	}
	settings := obj(sources[0], "settings")
	if _, ok := settings["initial_assignment_days"]; !ok {
		return true
	}
	created, occurred := timestamp(sources[0]["created_at"]), timestamp(event["occurred_at"])
	if created == nil || occurred == nil {
		return false
	}
	if occurred.After(*created) {
		return true
	}
	days := int(num(settings, "initial_assignment_days"))
	return days > 0 && !occurred.Before(created.Add(-time.Duration(days)*24*time.Hour))
}
