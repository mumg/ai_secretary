package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"mime/multipart"
	"net/http"
	"net/mail"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"golang.org/x/oauth2/jwt"
)

func (s *Server) job(ctx context.Context, fn func(*request) bool) (worked bool, err error) {
	defer func() {
		if v := recover(); v != nil {
			err = fmt.Errorf("worker operation failed (%T)", v)
			if e, ok := v.(*llmFailure); ok {
				err = e
			}
			if e, ok := v.(apiError); ok && e.Status < 500 {
				err = e
			}
		}
	}()
	tx := must(s.Pool.Begin(ctx))
	defer tx.Rollback(context.Background())
	q := &request{Context: ctx, db: tx, server: s}
	worked = fn(q)
	check(tx.Commit(ctx))
	s.deliver(ctx, q.notifications)
	return worked, nil
}
func (s *Server) Worker(ctx context.Context) {
	var wg sync.WaitGroup
	loop := func(name string, idle time.Duration, fn func(context.Context) bool) {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for ctx.Err() == nil {
				worked := fn(ctx)
				delay := idle
				if worked {
					delay = 100 * time.Millisecond
				}
				select {
				case <-ctx.Done():
					return
				case <-time.After(delay):
				}
			}
		}()
	}
	loop("events", 2*time.Second, s.processEvent)
	loop("chat", 2*time.Second, s.processChat)
	loop("contexts", 15*time.Second, s.processContext)
	loop("sources", 10*time.Second, s.syncSources)
	loop("maintenance", 30*time.Second, s.maintenance)
	wg.Wait()
}
func (s *Server) processEvent(ctx context.Context) bool {
	var eventID any
	attempts := 0
	worked, e := s.job(ctx, func(q *request) bool {
		// Give each queued message a turn before retrying the same failed mail.
		// Old failures must not consume every model slot during a large import.
		rows := q.rows("SELECT * FROM communication_events WHERE (analysis_state='PENDING' AND (next_analysis_at IS NULL OR next_analysis_at<=now())) OR (analysis_state='PROCESSING' AND updated_at<now()-interval '15 minutes') ORDER BY CASE event_type WHEN 'meeting_invitation' THEN 0 WHEN 'meeting_transcript' THEN 1 ELSE 2 END,(analysis_model IS NOT NULL),analysis_attempts,occurred_at FOR NO KEY UPDATE SKIP LOCKED LIMIT 1")
		if len(rows) == 0 {
			rows = q.rows(`SELECT * FROM communication_events e WHERE analysis_state='COMPLETED' AND event_type<>'meeting_invitation' AND NOT is_mailing AND (next_analysis_at IS NULL OR next_analysis_at<=now()) AND (semantic_version<2 OR NULLIF(btrim(semantic_summary),'') IS NULL OR (event_type='email' AND (mailing_version<1 OR NOT (analysis_result::jsonb ? 'task_extraction_version')))) ORDER BY occurred_at DESC FOR NO KEY UPDATE SKIP LOCKED LIMIT 1`)
		}
		if len(rows) == 0 {
			return false
		}
		event := rows[0]
		eventID = event["id"]
		attempts = int(num(event, "analysis_attempts")) + 1
		q.update("communication_events", eventID, M{"analysis_state": "PROCESSING", "analysis_attempts": attempts, "analysis_error": nil, "next_analysis_at": nil})
		return true
	})
	if e == nil && worked {
		// Commit the claim so status readers can see it. The row lock in this
		// transaction prevents stale-claim recovery from taking active work.
		worked, e = s.job(ctx, func(q *request) bool {
			rows := q.rows("SELECT * FROM communication_events WHERE id=$1 AND analysis_state='PROCESSING' FOR NO KEY UPDATE SKIP LOCKED", eventID)
			if len(rows) == 0 {
				return false
			}
			q.analyzeEvent(rows[0])
			return true
		})
	}
	if e != nil && eventID != nil {
		detail := "Ошибка обработки; будет повторено"
		var failure *llmFailure
		if errors.As(e, &failure) {
			detail = failure.Error() + "; будет повторено"
		}
		// Cancellation must also release a committed claim during shutdown.
		retryCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer cancel()
		delay := time.Duration(math.Min(30*math.Pow(2, float64(min(attempts-1, 7))), 3600)) * time.Second
		_, updateErr := s.Pool.Exec(retryCtx, "UPDATE communication_events SET analysis_state='PENDING',analysis_error=$2,analysis_attempts=$3,next_analysis_at=$4,updated_at=now() WHERE id=$1 AND analysis_state IN ('PENDING','PROCESSING','COMPLETED')", eventID, detail, attempts, time.Now().Add(delay))
		if updateErr != nil {
			slog.Error("cannot schedule event retry")
		}
		slog.Warn("event retry scheduled", "event_id", eventID, "attempt", attempts, "reason", detail)
	}
	return worked
}
func (q *request) identity() ([]string, []string) {
	names := stringsArray(obj(q.settings(), "identity")["names"])
	addresses := []string{}
	seen := map[string]bool{}
	for _, source := range q.rows("SELECT settings FROM communication_sources") {
		for _, key := range []string{"primary_smtp_address", "username"} {
			for _, a := range parseAddresses(str(obj(source, "settings"), key)) {
				if !seen[a] {
					seen[a] = true
					addresses = append(addresses, a)
				}
			}
		}
	}
	return names, addresses
}
func parseAddresses(s string) []string {
	addresses, e := mail.ParseAddressList(s)
	if e != nil {
		if strings.Contains(s, "@") && !strings.ContainsAny(s, " <>\t\n") {
			return []string{strings.ToLower(s)}
		}
		return nil
	}
	out := []string{}
	for _, a := range addresses {
		out = append(out, strings.ToLower(a.Address))
	}
	return out
}
func normalizeName(s string) string { return strings.ReplaceAll(strings.ToLower(clean(s)), "ё", "е") }
func hasName(text, name string) bool {
	tokens := regexp.MustCompile(`[\pL\pN_-]+`).FindAllString(normalizeName(text), -1)
	needle := " " + normalizeName(name) + " "
	return strings.Contains(" "+strings.Join(tokens, " ")+" ", needle)
}
func (q *request) assignment(event M, previous ...M) M {
	names, addresses := q.identity()
	return assignmentSignals(event, names, addresses, previous)
}
func (q *request) filtered(event M) M {
	headers := obj(event, "raw_headers")
	subject := strings.ToLower(str(event, "subject"))
	if auto := strings.ToLower(str(headers, "Auto-Submitted")); (auto != "" && auto != "no") || str(headers, "X-Autoreply") != "" || str(headers, "X-Autorespond") != "" {
		return M{"kind": "automatic_reply", "value": "Auto-Submitted", "state": "IGNORED"}
	}
	if regexp.MustCompile(`(?i)^\s*(automatic\s+reply|auto[ -]?reply|out\s+of\s+office|ooo|автоматический\s+ответ|автоответ|нет\s+на\s+рабочем\s+месте)\s*[:\-]`).MatchString(subject) {
		return M{"kind": "automatic_reply", "value": subject, "state": "IGNORED"}
	}
	if strings.EqualFold(str(headers, "Calendar-Method"), "REPLY") || regexp.MustCompile(`(?i)^\s*(accepted|declined|tentative|принято|отклонено|предварительно|под вопросом)\s*:`).MatchString(subject) {
		if !regexp.MustCompile(`(?i)(итоги|по итогам|протокол|договорил|решили|решения|follow.up|agreed|decided|action items)`).MatchString(str(event, "body")) {
			return M{"kind": "meeting_response", "value": subject, "state": "IGNORED"}
		}
	}
	filters := obj(q.settings(), "analysis_filters")
	for _, word := range stringsArray(filters["stop_words"]) {
		if strings.Contains(strings.ToLower(subject+"\n"+str(event, "body")), strings.ToLower(word)) {
			return M{"kind": "stop_word", "value": word, "state": "SKIPPED"}
		}
	}
	allAddresses := parseAddresses(str(event, "author"))
	data := must(json.Marshal(event["participants"]))
	var participants []M
	check(json.Unmarshal(data, &participants))
	for _, p := range participants {
		allAddresses = append(allAddresses, parseAddresses(str(p, "address"))...)
	}
	for _, excluded := range stringsArray(filters["excluded_addresses"]) {
		for _, a := range allAddresses {
			for _, b := range parseAddresses(excluded) {
				if a == b {
					return M{"kind": "address", "value": excluded, "state": "SKIPPED"}
				}
			}
		}
	}
	return nil
}
func (q *request) attachmentTexts(event M) []string {
	out := []string{}
	cfg := obj(q.settings(), "document_parser")
	for _, a := range q.rows("SELECT * FROM attachments WHERE event_id=$1", event["id"]) {
		if a["extraction_state"] == "EXTRACTED" {
			if text := str(a, "extracted_text"); text != "" {
				out = append(out, text)
			}
			continue
		}
		path := str(a, "storage_path")
		suffix := strings.ToLower(filepath.Ext(path))
		if suffix != ".pdf" && suffix != ".docx" && suffix != ".xlsx" {
			q.update("attachments", a["id"], M{"extraction_state": "UNSUPPORTED"})
			continue
		}
		data, e := os.ReadFile(path)
		if e != nil || len(data) > int(num(cfg, "max_bytes")) {
			q.update("attachments", a["id"], M{"extraction_state": "FAILED", "extraction_error": "Attachment unavailable or exceeds size limit"})
			continue
		}
		var body bytes.Buffer
		writer := multipart.NewWriter(&body)
		part := must(writer.CreateFormFile("file", filepath.Base(path)))
		_, e = part.Write(data)
		check(e)
		check(writer.Close())
		req := must(http.NewRequestWithContext(q.Context, "POST", strings.TrimRight(q.server.Config.ParserURL, "/")+"/extract", &body))
		req.Header.Set("Content-Type", writer.FormDataContentType())
		client := http.Client{Timeout: time.Duration(num(cfg, "timeout_seconds")) * time.Second}
		response, e := client.Do(req)
		if e != nil {
			panic(e)
		}
		var result M
		e = json.NewDecoder(io.LimitReader(response.Body, 2<<20)).Decode(&result)
		response.Body.Close()
		check(e)
		if response.StatusCode != 200 {
			panic(fmt.Errorf("document parser HTTP %d", response.StatusCode))
		}
		text := bounded(str(result, "text"), int(num(cfg, "max_characters")))
		q.update("attachments", a["id"], M{"extraction_state": "EXTRACTED", "extracted_text": text, "extraction_error": nil})
		out = append(out, text)
	}
	return out
}
func (q *request) analyzeEvent(event M) {
	now := q.now()
	if str(event, "thread_external_id") == "" {
		event = q.update("communication_events", event["id"], M{"thread_external_id": event["external_id"]})
	}
	if event["event_type"] == "meeting_invitation" {
		q.upsertMeeting(event)
		q.update("communication_events", event["id"], M{"analysis_state": "COMPLETED", "analysis_error": nil, "next_analysis_at": nil, "analysis_result": M{"calendar_event": true}, "analyzed_at": now, "semantic_version": 2, "semantic_summary": event["subject"]})
		q.rebuildPlan()
		return
	}
	if filter := q.filtered(event); filter != nil {
		q.update("communication_events", event["id"], M{"analysis_state": filter["state"], "analysis_error": nil, "next_analysis_at": nil, "analysis_result": M{"skipped": true, "filter": pick(filter, "kind", "value")}, "analyzed_at": now, "semantic_summary": bounded(str(event, "subject"), 8000), "semantic_categories": []string{"Исключено из автоматического анализа"}, "semantic_version": 2})
		q.rebuildThread(event, M{})
		return
	}
	event = q.reconcileThread(event)
	attachments := q.attachmentTexts(event)
	contextRows := q.rows("SELECT id,occurred_at,author,direction,subject,body,participants FROM communication_events WHERE source_id=$1 AND thread_external_id=$2 AND id<>$3 AND occurred_at<=$4 AND NOT is_mailing AND analysis_state NOT IN ('IGNORED','SKIPPED') ORDER BY occurred_at DESC LIMIT 8", event["source_id"], event["thread_external_id"], event["id"], event["occurred_at"])
	for _, row := range contextRows {
		row["body"] = bounded(str(row, "body"), 4000)
	}
	tasks := q.rows("SELECT t.* FROM tasks t JOIN communication_events e ON e.id=t.source_event_id WHERE e.source_id=$1 AND e.thread_external_id=$2 AND t.status NOT IN ('CANCELLED','COMPLETED')", event["source_id"], event["thread_external_id"])
	assignment := q.assignment(event, contextRows...)
	names, addresses := q.identity()
	settings := q.settings()
	llm := obj(settings, "llm")
	budget := max(8000, int(num(llm, "context_length"))*3)
	payload := M{"current_datetime": now, "timezone": obj(settings, "server")["timezone"], "user_identity": M{"names": names, "addresses": addresses}, "assignment_signals": assignment, "workday": obj(settings, "calendar"), "source_type": event["source_type"], "event_type": event["event_type"], "direction": event["direction"], "subject": event["subject"], "author": event["author"], "participants": event["participants"], "body": bounded(str(event, "body"), budget/2), "attachments_text": attachments, "previous_events_in_thread": contextRows, "active_tasks_in_thread": tasks}
	if event["event_type"] == "email" {
		payload["body"] = bounded(cleanEmail(str(event, "body")), budget/2)
	}
	analysis := must(q.llm(str(obj(llmDefinitions, "prompts"), "analyze"), payload, "AnalysisResult", nil))
	if str(analysis, "summary") == "" || str(analysis, "thread_summary") == "" {
		panic(fmt.Errorf("invalid semantic analysis"))
	}
	mailing := obj(analysis, "mailing")
	isMailing := event["event_type"] == "email" && boolean(mailing, "detected") && num(mailing, "confidence") >= 0.8
	candidates, _ := analysis["tasks"].([]any)
	if !isMailing && event["event_type"] == "email" && boolean(assignment, "eligible") && len(candidates) == 0 {
		focused := must(q.llm(str(obj(llmDefinitions, "prompts"), "extract_tasks"), payload, "TaskExtractionResult", nil))
		candidates, _ = focused["tasks"].([]any)
	}
	if !isMailing && boolean(assignment, "eligible") {
		for _, value := range candidates {
			candidate, ok := value.(map[string]any)
			if !ok {
				panic(fmt.Errorf("invalid task candidate"))
			}
			q.taskCandidate(event, candidate, assignment, addresses, names, llm, contextRows...)
		}
	}
	if event["direction"] == "OUTGOING" && !isMailing {
		if completions, ok := analysis["completion_candidates"].([]any); ok {
			for _, value := range completions {
				c, ok := value.(map[string]any)
				if !ok || num(c, "confidence") < num(llm, "possible_completion_confidence") {
					continue
				}
				for _, t := range tasks {
					if t["id"] == c["task_id"] {
						q.update("tasks", t["id"], M{"status": "POSSIBLY_COMPLETED", "evidence": c["evidence"]})
					}
				}
			}
		}
	}
	update := M{"analysis_state": "COMPLETED", "analysis_error": nil, "next_analysis_at": nil, "analysis_model": llm["model"], "analysis_result": analysis, "analyzed_at": now, "semantic_version": 2, "semantic_summary": bounded(str(analysis, "thread_summary"), 8000)}
	analysis["assignment_signals"] = assignment
	analysis["task_extraction_version"] = 1
	index := []string{str(update, "semantic_summary")}
	for key, limit := range map[string]int{"categories": 12, "keywords": 30, "people": 30, "organizations": 20, "decisions": 20, "agreements": 20} {
		values := stringsArray(analysis[key])
		if len(values) > limit {
			values = values[:limit]
		}
		update["semantic_"+key] = values
		index = append(index, values...)
	}
	update["semantic_index"] = strings.ToLower(strings.Join(index, "\n"))
	if event["event_type"] == "email" {
		update["is_mailing"] = isMailing
		update["mailing_confidence"] = mailing["confidence"]
		update["mailing_kind"] = mailing["kind"]
		update["mailing_version"] = 1
		update["mailing_analyzed_at"] = now
	}
	event = q.update("communication_events", event["id"], update)
	if event["event_type"] == "meeting_transcript" {
		q.exec("UPDATE meeting_results SET summary=$2,decisions=$3,agreements=$4,analyzed_at=now(),updated_at=now() WHERE source_event_id=$1", event["id"], analysis["summary"], must(json.Marshal(update["semantic_decisions"])), must(json.Marshal(update["semantic_agreements"])))
		for _, result := range q.rows("SELECT * FROM meeting_results WHERE source_event_id=$1", event["id"]) {
			q.linkTranscript(result, event, q.calendarCandidates())
		}
		q.consolidateResults()
	} else {
		if !isMailing {
			q.recordEmailResult(event, analysis)
		}
		q.rebuildThread(event, analysis)
	}
	q.update("communication_events", event["id"], M{"analyzed_at": q.now()})
	q.rebuildPlan()
}
func (q *request) taskCandidate(event, candidate, assignment M, addresses, names []string, llm M, previous ...M) {
	if candidate["assignee"] == "other" {
		return
	}
	address := strings.ToLower(str(candidate, "assignee_address"))
	if address != "" {
		found := false
		for _, a := range addresses {
			found = found || a == address
		}
		if !found {
			return
		}
	}
	validateTask(candidate, true)
	evidence := clean(str(candidate, "evidence"))
	assignmentEvidence := clean(str(candidate, "assignment_evidence"))
	owner := taskOwner(event, evidence, names, addresses, previous...)
	if owner == "other" || owner == "stale" {
		return
	}
	if event["event_type"] == "meeting_transcript" {
		owner = transcriptOwner(event, assignmentEvidence, evidence, names, addresses)
		if owner == "other" || owner == "unproven" {
			return
		}
	}
	for _, match := range regexp.MustCompile(`[\pL\pN._%+\-]+@[\pL\pN.\-]+`).FindAllString(assignmentEvidence, -1) {
		own := false
		for _, a := range addresses {
			own = own || strings.EqualFold(match, a)
		}
		if !own {
			return
		}
	}
	duplicate := q.rows("SELECT t.* FROM tasks t JOIN communication_events e ON e.id=t.source_event_id WHERE lower(t.title)=lower($1) AND e.source_id=$2 AND (($3::text IS NOT NULL AND e.thread_external_id=$3) OR ($3::text IS NULL AND t.source_event_id=$4)) AND (t.status NOT IN ('CANCELLED','COMPLETED') OR (t.status='CANCELLED' AND t.source_event_id=$4)) ORDER BY t.created_at,t.id LIMIT 1", candidate["title"], event["source_id"], event["thread_external_id"], event["id"])
	if len(duplicate) > 0 {
		task := duplicate[0]
		if event["event_type"] == "email" && event["direction"] == "INCOMING" && task["status"] != "CANCELLED" && !boolean(task, "manually_created") {
			previous := q.get("communication_events", str(task, "source_event_id"))
			incoming, old := timestamp(event["occurred_at"]), timestamp(previous["occurred_at"])
			if incoming != nil && old != nil && incoming.After(*old) {
				update := M{"source_event_id": event["id"], "evidence": candidate["evidence"], "confidence": candidate["confidence"]}
				if str(candidate, "description") != "" {
					update["description"] = candidate["description"]
				}
				if task["due_at"] == nil {
					update["due_at"] = q.normalizeDue(candidate["due_at"])
				}
				q.update("tasks", task["id"], update)
			}
		}
		return
	}
	auto := owner != "uncertain" && candidate["assignee"] == "user" && !boolean(assignment, "name_ambiguous") && num(candidate, "confidence") >= num(llm, "auto_create_confidence")
	status := "NEEDS_CONFIRMATION"
	if auto {
		status = "NEW"
	}
	priority := str(candidate, "priority")
	if priority == "" {
		priority = "NORMAL"
	}
	headers := obj(event, "raw_headers")
	prioritySource := "LLM"
	if event["event_type"] == "email" && (strings.EqualFold(str(headers, "Importance"), "high") || strings.HasPrefix(str(headers, "X-Priority"), "1") || strings.EqualFold(str(headers, "Priority"), "urgent")) {
		priority = "HIGH"
		prioritySource = "SOURCE"
	}
	q.insert("tasks", M{"title": candidate["title"], "description": candidate["description"], "status": status, "priority": priority, "priority_source": prioritySource, "due_at": q.normalizeDue(candidate["due_at"]), "source_event_id": event["id"], "evidence": candidate["evidence"], "confidence": candidate["confidence"], "manually_created": false})
}
func (q *request) rebuildThread(event, analysis M) {
	rows := q.rows("SELECT * FROM communication_events e WHERE source_id=$1 AND thread_external_id=$2 AND "+visibleEvent+" ORDER BY occurred_at,id", event["source_id"], event["thread_external_id"])
	if len(rows) == 0 {
		q.exec("DELETE FROM conversation_threads WHERE source_id=$1 AND thread_external_id=$2", event["source_id"], event["thread_external_id"])
		return
	}
	first, last := rows[0], rows[len(rows)-1]
	// Index pending mail immediately, retaining the last available summary until
	// the new message has been analyzed. Never mark pending mail as summarized.
	var summary, summaryModel, summarizedAt any
	for i := len(rows) - 1; i >= 0; i-- {
		message := rows[i]
		text := str(message, "semantic_summary")
		if message["id"] == event["id"] && str(analysis, "thread_summary") != "" {
			text = str(analysis, "thread_summary")
		}
		if text != "" {
			summary = text
			summaryModel = message["analysis_model"]
			summarizedAt = message["analyzed_at"]
			break
		}
	}
	last = copyMap(last)
	last["subject"] = subjectTitle(str(last, "subject"))
	people := []M{}
	seen := map[string]bool{}
	for _, message := range rows {
		for _, person := range roster(message) {
			key := strings.ToLower(str(person, "address") + str(person, "email") + str(person, "name"))
			if !seen[key] && len(people) < 100 {
				seen[key] = true
				people = append(people, person)
			}
		}
	}
	last["participants"] = people
	q.exec("INSERT INTO conversation_threads (id,source_id,source_type,thread_external_id,title,participants,summary,event_count,first_event_at,last_event_at,latest_event_id,summary_model,summarized_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT(source_id,thread_external_id) DO UPDATE SET title=EXCLUDED.title,participants=EXCLUDED.participants,summary=EXCLUDED.summary,event_count=EXCLUDED.event_count,first_event_at=EXCLUDED.first_event_at,last_event_at=EXCLUDED.last_event_at,latest_event_id=EXCLUDED.latest_event_id,summary_model=EXCLUDED.summary_model,summarized_at=EXCLUDED.summarized_at,updated_at=now()", newID(), event["source_id"], event["source_type"], event["thread_external_id"], last["subject"], must(json.Marshal(last["participants"])), summary, len(rows), first["occurred_at"], last["occurred_at"], last["id"], summaryModel, summarizedAt)
}
func (q *request) upsertMeeting(event M) {
	calendar := obj(obj(event, "raw_headers"), "Calendar-Event")
	if str(calendar, "uid") == "" {
		return
	}
	dateField(calendar, "starts_at", true)
	dateField(calendar, "ends_at", true)
	values := pick(calendar, "title", "starts_at", "ends_at", "all_day", "location", "organizer", "attendees", "status", "method")
	values["source_event_id"] = event["id"]
	values["last_event_at"] = event["occurred_at"]
	urls := mtsURLs(str(calendar, "location"), str(event, "body"), str(event, "source_url"))
	values["mts_link_keys"] = q.mtsReferenceKeys(urls)
	if len(urls) > 0 {
		values["mts_link_url"] = urls[0]
	}
	rows := q.rows("SELECT * FROM meetings WHERE source_id=$1 AND external_uid=$2 FOR UPDATE", event["source_id"], calendar["uid"])
	if len(rows) == 0 {
		values["source_id"] = event["source_id"]
		values["external_uid"] = calendar["uid"]
		meeting := q.insert("meetings", values)
		q.refreshResultLinks(meeting)
	} else {
		previous := timestamp(rows[0]["last_event_at"])
		incoming := timestamp(event["occurred_at"])
		if previous == nil || incoming == nil || !incoming.Before(*previous) {
			meeting := q.update("meetings", rows[0]["id"], values)
			q.refreshResultLinks(meeting)
		}
	}
}
func (s *Server) processChat(ctx context.Context) bool {
	var id any
	attempt := 0
	worked, e := s.job(ctx, func(q *request) bool {
		rows := q.rows("SELECT * FROM chat_requests WHERE (status='PENDING' AND (next_attempt_at IS NULL OR next_attempt_at<=now())) OR (status='PROCESSING' AND started_at<now()-interval '15 minutes') ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1")
		if len(rows) == 0 {
			return false
		}
		m := rows[0]
		id = m["id"]
		attempt = int(num(m, "attempts")) + 1
		q.update("chat_requests", id, M{"status": "PROCESSING", "started_at": time.Now(), "attempts": attempt})
		result := q.archiveAnswer(m, nil)
		q.update("chat_requests", id, M{"status": "COMPLETED", "answer": result["answer"], "references": result["references"], "error": nil, "next_attempt_at": nil, "completed_at": time.Now()})
		return true
	})
	if e != nil && id != nil {
		if cause, ok := e.(apiError); ok && cause.Status == 422 {
			_, _ = s.Pool.Exec(ctx, "UPDATE chat_requests SET status='FAILED',attempts=$2,error=$3,next_attempt_at=NULL,updated_at=now() WHERE id=$1", id, attempt, cause.Detail)
			return true
		}
		_, _ = s.Pool.Exec(ctx, "UPDATE chat_requests SET status='PENDING',attempts=$2,error='Ошибка LLM; будет повторено',next_attempt_at=$3,updated_at=now() WHERE id=$1", id, attempt, time.Now().Add(time.Duration(math.Min(30*math.Pow(2, float64(min(attempt-1, 7))), 3600))*time.Second))
	}
	return worked
}
func (s *Server) processContext(ctx context.Context) bool {
	worked, e := s.job(ctx, func(q *request) bool {
		rows := q.rows("SELECT c.*,m.title FROM meeting_contexts c JOIN meetings m ON m.id=c.meeting_id WHERE m.ends_at>now() AND m.status<>'CANCELLED' AND (c.status='PENDING' OR (c.requested_at IS NOT NULL AND c.next_refresh_at<=now())) ORDER BY m.starts_at FOR UPDATE OF c SKIP LOCKED LIMIT 1")
		if len(rows) == 0 {
			return false
		}
		m := rows[0]
		q.exec("UPDATE meeting_contexts SET status='PROCESSING',started_at=now(),generation=$2 WHERE meeting_id=$1", m["meeting_id"], newID())
		answer := q.archiveAnswer(M{"query": m["title"], "history": []any{}, "tag_ids": []any{}, "literal_topic": true, "before": q.now()}, nil)
		q.exec(`UPDATE meeting_contexts SET status=CASE WHEN json_array_length($3::json)>0 THEN 'READY' ELSE 'EMPTY' END,summary=$2,"references"=$3,input_fingerprint=$4,generated_at=now(),notify_after=CASE WHEN requested_at IS NOT NULL THEN now() ELSE NULL END,next_refresh_at=now()+interval '15 minutes',error=NULL,updated_at=now() WHERE meeting_id=$1`, m["meeting_id"], answer["answer"], must(json.Marshal(answer["references"])), hash(answer))
		return true
	})
	if e != nil {
		slog.Warn("meeting context retry pending")
	}
	return worked
}
func (s *Server) syncSources(ctx context.Context) bool {
	_, e := s.job(ctx, func(q *request) bool {
		sources := q.rows("SELECT * FROM communication_sources WHERE enabled AND source_type<>'external_tasks' ORDER BY label")
		for _, source := range sources {
			interval := num(obj(source, "settings"), "poll_interval_seconds")
			if interval == 0 {
				interval = num(obj(q.settings(), "worker"), "poll_interval_seconds")
			}
			if last := timestamp(source["last_sync_at"]); last != nil && time.Since(*last) < time.Duration(interval)*time.Second {
				continue
			}
			id := str(source, "id")
			if source["last_error"] != nil {
				rows := q.rows("SELECT observed_at FROM component_statuses WHERE id=$1", "source-"+id)
				if len(rows) > 0 {
					if last := timestamp(rows[0]["observed_at"]); last != nil && time.Since(*last) < time.Duration(interval)*time.Second {
						continue
					}
				}
			}
			_, syncErr := s.job(ctx, func(sourceQ *request) bool {
				sourceQ.exec("SELECT pg_advisory_xact_lock(hashtext($1))", "source:"+id)
				source = sourceQ.one("SELECT * FROM communication_sources WHERE id=$1 AND enabled", id)
				switch source["source_type"] {
				case "imap":
					sourceQ.imapSync(source, false)
				case "exchange":
					sourceQ.exchangeSync(source, false)
				case "mts_link":
					sourceQ.mtsSync(source, false)
				}
				sourceQ.update("communication_sources", id, M{"last_sync_at": time.Now(), "last_error": nil})
				sourceQ.component("source-"+id, M{"label": source["label"], "component_type": "event_loader", "status": "OK", "metrics": M{}, "ttl_seconds": max(900, interval*3)})
				return true
			})
			if syncErr != nil {
				q.update("communication_sources", id, M{"last_error": "Ошибка загрузки данных; проверьте соединение и учётные данные"})
				q.component("source-"+id, M{"label": source["label"], "component_type": "event_loader", "status": "ERROR", "message": "Ошибка загрузки данных", "metrics": M{}, "ttl_seconds": max(900, interval*3)})
			}
		}
		return false
	})
	if e != nil {
		slog.Warn("source sync failed")
	}
	return false
}

// Repair archives imported by versions that indexed threads only after LLM analysis.
// Bounded batches also allow an interrupted repair to resume without changing mail cursors.
func (q *request) indexMissingThreads() {
	rows := q.rows("SELECT DISTINCT ON (e.source_id,e.thread_external_id) e.* FROM communication_events e WHERE " + visibleEvent + " AND NULLIF(e.thread_external_id,'') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM conversation_threads t WHERE t.source_id=e.source_id AND t.thread_external_id=e.thread_external_id) ORDER BY e.source_id,e.thread_external_id,e.occurred_at DESC,e.id DESC LIMIT 1000")
	for _, event := range rows {
		q.rebuildThread(event, M{})
	}
}

func (s *Server) maintenance(ctx context.Context) bool {
	// Keep archive repair outside the plan lock: analysis can update threads
	// before taking that lock, so sharing a transaction would invert lock order.
	if _, err := s.job(ctx, func(q *request) bool { q.indexMissingThreads(); return false }); err != nil {
		slog.Warn("mail thread indexing will be retried")
	}
	_, e := s.job(ctx, func(q *request) bool {
		q.exec("SELECT pg_advisory_xact_lock(726941831)")
		q.component("worker-main", M{"label": "Фоновая обработка", "component_type": "worker", "status": "OK", "metrics": M{}, "ttl_seconds": 180})
		now := q.now()
		settings := q.settings()
		plans := q.rows("SELECT * FROM daily_plans WHERE plan_date=$1", now.Format("2006-01-02"))
		if len(plans) == 0 && now.Format("15:04") >= str(obj(settings, "calendar"), "daily_plan_time") {
			plan := q.rebuildPlan()
			q.notifications = append(q.notifications, [2]string{"DAILY_PLAN_READY", str(plan, "id")})
		} else if len(plans) > 0 {
			generated := timestamp(plans[0]["generated_at"])
			if generated == nil || now.Sub(*generated) >= time.Duration(num(obj(settings, "worker"), "ranking_interval_seconds"))*time.Second {
				q.rebuildPlan()
			}
		}
		for _, m := range q.rows("SELECT id FROM meetings WHERE starts_at<$1 AND ends_at>$2 AND status<>'CANCELLED'", time.Date(now.Year(), now.Month(), now.Day()+1, 0, 0, 0, 0, now.Location()), now) {
			q.meetingContext(str(m, "id"), false)
			q.exec("UPDATE meeting_contexts SET status='PENDING',updated_at=now() WHERE meeting_id=$1 AND status='NOT_REQUESTED'", m["id"])
		}
		for _, r := range q.rows("SELECT r.* FROM reminders r JOIN tasks t ON t.id=r.task_id WHERE r.enabled AND r.sent_at IS NULL AND r.remind_at<=now() AND t.status NOT IN ('COMPLETED','CANCELLED') FOR UPDATE OF r SKIP LOCKED") {
			if q.notify("TASK_REMINDER", str(r, "task_id")) > 0 {
				q.update("reminders", r["id"], M{"sent_at": time.Now()})
			}
		}
		dueUntil := now.Add(time.Duration(num(obj(settings, "notifications"), "due_soon_minutes")) * time.Minute)
		for _, task := range q.rows("SELECT * FROM tasks WHERE status IN ('NEW','IN_PROGRESS') AND due_at>$1 AND due_at<=$2 AND due_reminder_sent_at IS NULL FOR UPDATE SKIP LOCKED", now, dueUntil) {
			if q.notify("TASK_DUE_SOON", str(task, "id")) > 0 {
				q.update("tasks", task["id"], M{"due_reminder_sent_at": now})
			}
		}
		if now.Hour() >= int(num(obj(settings, "notifications"), "overdue_repeat_hour")) {
			date := now.Format("2006-01-02")
			for _, task := range q.rows("SELECT * FROM tasks WHERE status IN ('NEW','IN_PROGRESS') AND due_at<$1 AND (overdue_notification_date IS NULL OR overdue_notification_date<>$2) FOR UPDATE SKIP LOCKED", now, date) {
				if q.notify("TASK_OVERDUE", str(task, "id")) > 0 {
					q.update("tasks", task["id"], M{"overdue_notification_date": date})
				}
			}
		}
		for _, context := range q.rows("SELECT c.meeting_id,m.status,m.ends_at FROM meeting_contexts c JOIN meetings m ON m.id=c.meeting_id WHERE c.notify_after<=now() AND c.status IN ('READY','EMPTY') FOR UPDATE OF c SKIP LOCKED") {
			var next any = time.Now().Add(5 * time.Minute)
			if context["status"] == "CANCELLED" || !timestamp(context["ends_at"]).After(time.Now()) || q.notify("MEETING_CONTEXT_READY", str(context, "meeting_id")) > 0 {
				next = nil
			}
			q.exec("UPDATE meeting_contexts SET notify_after=$2 WHERE meeting_id=$1", context["meeting_id"], next)
		}

		return false
	})
	if e != nil {
		slog.Warn("maintenance failed")
	}
	return false
}
func (q *request) notify(kind, id string) int {
	delivered := 0
	if q.server.Config.LocalOnly {
		return 0
	}
	rows := q.rows("SELECT firebase_credentials_encrypted FROM system_settings WHERE id=1")
	if len(rows) == 0 || str(rows[0], "firebase_credentials_encrypted") == "" {
		return 0
	}
	plain := must(q.server.Config.Decrypt(str(rows[0], "firebase_credentials_encrypted")))
	var account M
	check(json.Unmarshal([]byte(plain), &account))
	cfg := jwt.Config{Email: str(account, "client_email"), PrivateKey: []byte(str(account, "private_key")), PrivateKeyID: str(account, "private_key_id"), Scopes: []string{"https://www.googleapis.com/auth/firebase.messaging"}, TokenURL: str(account, "token_uri")}
	client := cfg.Client(q.Context)
	client.Timeout = 30 * time.Second
	data := M{"type": kind, "object_id": id}
	if strings.Contains(kind, "TASK") {
		tasks := q.rows("SELECT title,description FROM tasks WHERE id=$1", id)
		if len(tasks) > 0 {
			data["task_title"] = bounded(str(tasks[0], "title"), 180)
			data["task_description"] = bounded(clean(str(tasks[0], "description")), 360)
		}
	}
	for _, device := range q.rows("SELECT fcm_token FROM devices WHERE active") {
		body := must(json.Marshal(M{"message": M{"token": device["fcm_token"], "data": data, "android": M{"priority": "high"}}}))
		req := must(http.NewRequestWithContext(q.Context, "POST", "https://fcm.googleapis.com/v1/projects/"+str(account, "project_id")+"/messages:send", bytes.NewReader(body)))
		req.Header.Set("Content-Type", "application/json")
		response, e := client.Do(req)
		if e != nil {
			slog.Warn("notification failed", "type", kind)
			continue
		}
		response.Body.Close()
		if response.StatusCode < 300 {
			delivered++
		}
		if response.StatusCode >= 300 {
			slog.Warn("notification failed", "type", kind, "status", response.StatusCode)
		}
	}
	return delivered
}

func (s *Server) deliver(ctx context.Context, notifications [][2]string) {
	defer func() {
		if recover() != nil {
			slog.Warn("notification delivery failed after commit")
		}
	}()
	if len(notifications) == 0 {
		return
	}
	q := &request{Context: ctx, db: s.Pool, server: s}
	for _, n := range notifications {
		q.notify(n[0], n[1])
	}
}
