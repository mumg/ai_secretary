package server

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

//go:embed search_data.json
var searchData []byte
var searchWords M

func init() { check(json.Unmarshal(searchData, &searchWords)) }
func searchTokens(s string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, word := range append(stringsArray(searchWords["STOP_WORDS"]), strings.Fields("от по на мы он об за до из со не ли же")...) {
		seen[word] = true
	}
	for _, word := range regexp.MustCompile(`[\pL\pN_@.\-]+`).FindAllString(strings.ToLower(s), -1) {
		word = strings.Trim(word, "._-@")
		if len([]rune(word)) < 2 || seen[word] {
			continue
		}
		domain := false
		for _, prefix := range stringsArray(searchWords["DOMAIN_WORD_PREFIXES"]) {
			domain = domain || strings.HasPrefix(word, prefix)
		}
		if domain {
			continue
		}
		seen[word] = true
		out = append(out, word)
		if len(out) == 10 {
			break
		}
	}
	return out
}
func searchAliases(word string) []string {
	out := []string{word}
	for _, suffix := range stringsArray(searchWords["RUSSIAN_SEARCH_SUFFIXES"]) {
		if strings.HasSuffix(word, suffix) && len([]rune(word))-len([]rune(suffix)) >= 4 {
			out = append(out, strings.TrimSuffix(word, suffix))
			break
		}
	}
	for _, alias := range append([]string{}, out...) {
		var b strings.Builder
		for _, r := range alias {
			v := str(obj(searchWords, "CYRILLIC_TO_LATIN"), string(r))
			if v == "" {
				v = string(r)
			}
			b.WriteString(v)
		}
		if b.String() != alias {
			out = append(out, b.String())
		}
	}
	return out
}

type searchIntent struct {
	terms, entity, statuses []string
	scope                   string
	start, end              *time.Time
	overdue                 bool
}

func resolveSearch(query string, history any) string {
	if !rx(`(?i)^(?:а\b|кто отвечает|когда\b|какой срок)|\b(?:этому|него|нему|там|этой|этого)\b`, query) {
		return query
	}
	var rows []M
	_ = json.Unmarshal(must(json.Marshal(history)), &rows)
	previous := []string{}
	for i := len(rows) - 1; i >= 0 && len(previous) < 3; i-- {
		if rows[i]["role"] != "user" {
			continue
		}
		s := bounded(str(rows[i], "content"), 500)
		previous = append([]string{s}, previous...)
		if !rx(`(?i)^(?:а\b|кто отвечает|когда\b|какой срок)`, s) {
			break
		}
	}
	return strings.Join(append(previous, query), "\n")
}
func parseSearch(query string, now time.Time, literal bool) searchIntent {
	intent := searchIntent{scope: "all"}
	text := strings.ToLower(query)
	if literal {
		intent.scope = "events"
	} else {
		events := rx(`\b(?:писем|письм|сообщен|переписк|встреч|расшифров)`, text)
		tasks := rx(`\bзадач`, text)
		delegations := rx(`\b(?:поручен|поручил|делегир)`, text)
		if events && !tasks && !delegations {
			intent.scope = "events"
		}
		if delegations && !tasks && !events {
			intent.scope = "delegations"
		}
		if tasks && !events && !delegations {
			intent.scope = "tasks"
		}
		day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, now.Location())
		weekday := (int(day.Weekday()) + 6) % 7
		windows := []struct {
			pattern    string
			start, end time.Time
		}{
			{`(?:с|за|на|в)?\s*прошл\w*\s+недел\w*`, day.AddDate(0, 0, -weekday-7), day.AddDate(0, 0, -weekday)},
			{`(?:за|на|в)?\s*(?:эт\w*|текущ\w*)\s+недел\w*`, day.AddDate(0, 0, -weekday), day.AddDate(0, 0, 7-weekday)},
			{`\bвчера\b`, day.AddDate(0, 0, -1), day}, {`\bсегодня\b`, day, day.AddDate(0, 0, 1)}, {`\bзавтра\b`, day.AddDate(0, 0, 1), day.AddDate(0, 0, 2)},
		}
		for _, window := range windows {
			if rx(window.pattern, text) {
				intent.start = &window.start
				intent.end = &window.end
				r := must(compileLinkPattern(window.pattern))
				text = must(r.Replace(text, " ", -1, -1))
				break
			}
		}
		r := must(compileLinkPattern(`(?:за\s+)?последни[ех]\s+(\d{1,3})\s+дн\w*`))
		if m := must(r.FindStringMatch(text)); m != nil {
			n, _ := strconv.Atoi(m.GroupByNumber(1).String())
			start := now.AddDate(0, 0, -n)
			intent.start = &start
			intent.end = &now
			text = must(r.Replace(text, " ", -1, -1))
		}
		intent.overdue = rx(`просроч\w*`, text)
		if intent.overdue || rx(`незаверш\w*|невыполн\w*|не\s+(?:выполн\w*|заверш\w*)|активн\w*|открыт\w*`, text) {
			intent.statuses = []string{"NEW", "IN_PROGRESS", "NEEDS_CONFIRMATION", "POSSIBLY_COMPLETED"}
		} else if rx(`на\s+проверк\w*`, text) {
			intent.statuses = []string{"POSSIBLY_COMPLETED"}
		} else if rx(`в\s+работе`, text) {
			intent.statuses = []string{"IN_PROGRESS"}
		} else if rx(`назначен\w*`, text) {
			intent.statuses = []string{"NEW"}
		} else if rx(`выполн\w*|заверш\w*`, text) {
			intent.statuses = []string{"COMPLETED"}
		} else if rx(`отмен[её]н\w*`, text) {
			intent.statuses = []string{"CANCELLED"}
		}
		r = must(compileLinkPattern(`на\s+проверк\w*|в\s+работе|назначен\w*|просроч\w*|незаверш\w*|невыполн\w*|не\s+(?:выполн\w*|заверш\w*)|активн\w*|открыт\w*|выполн\w*|заверш\w*|отмен[её]н\w*`))
		text = must(r.Replace(text, " ", -1, -1))
		r = must(compileLinkPattern(`\b(?:от|from|с|with)\s+([\w@.+-]+)`))
		if m := must(r.FindStringMatch(text)); m != nil {
			token := strings.TrimRight(m.GroupByNumber(1).String(), ".")
			if !rx(`^(?:прошл|последн|начал|конц|понедель|вторник|сред|четверг|пятниц|суббот|воскрес|утра|\d)`, token) {
				if tokens := searchTokens(token); len(tokens) > 0 {
					intent.entity = searchAliases(tokens[0])
				}
			}
		}
	}
	for _, token := range searchTokens(text) {
		intent.terms = append(intent.terms, searchAliases(token)...)
	}
	return intent
}
func (q *request) retrieveArchive(m M) ([]M, []M) {
	intent := parseSearch(resolveSearch(str(m, "query"), m["history"]), q.now(), boolean(m, "literal_topic"))
	if before := timestamp(m["before"]); before != nil && (intent.end == nil || before.Before(*intent.end)) {
		intent.end = before
	}
	retrieve := func(tasks, delegations bool) []M {
		args := []any{stringsArray(m["tag_ids"])}
		add := func(v any) string { args = append(args, v); return fmt.Sprintf("$%d", len(args)) }
		where := []string{"(cardinality($1::text[])=0 OR EXISTS(SELECT 1 FROM communication_source_tags st WHERE st.source_id=e.source_id AND st.tag_id::text=ANY($1::text[])))"}
		table, date := "e", "e.occurred_at"
		query := "SELECT e.*,s.label AS source_label FROM communication_events e LEFT JOIN communication_sources s ON s.id=e.source_id"
		order := "e.occurred_at DESC"
		limit := 24
		if tasks {
			table, date = "t", "t.due_at"
			query = "SELECT t.*,s.label AS source_label,e.source_url,e.author,e.occurred_at FROM tasks t LEFT JOIN communication_events e ON e.id=t.source_event_id LEFT JOIN communication_sources s ON s.id=e.source_id"
			if delegations {
				query = "SELECT t.*, 'delegation' AS record_kind,s.label AS source_label,e.source_url,e.author,e.occurred_at FROM delegations t LEFT JOIN communication_events e ON e.id=t.source_event_id LEFT JOIN communication_sources s ON s.id=e.source_id"
			}
			order = "t.updated_at DESC"
			limit = 12
		} else {
			where = append(where, "e.analysis_state NOT IN ('IGNORED','SKIPPED') AND NOT e.is_mailing")
			if boolean(m, "literal_topic") {
				where = append(where, "e.event_type NOT IN ('meeting_invitation','meeting_transcript')")
			}
		}
		if intent.start != nil {
			where = append(where, date+">="+add(*intent.start))
		}
		if intent.end != nil {
			where = append(where, date+"<"+add(*intent.end))
		}
		if tasks && len(intent.statuses) > 0 {
			where = append(where, "t.status=ANY("+add(func() []string {
				if !delegations {
					return intent.statuses
				}
				out := []string{}
				for _, status := range intent.statuses {
					switch status {
					case "NEW", "NEEDS_CONFIRMATION":
						out = append(out, "ASSIGNED")
					case "POSSIBLY_COMPLETED":
						out = append(out, "IN_REVIEW")
					default:
						out = append(out, status)
					}
				}
				return out
			}())+"::text[])")
		}
		if tasks && intent.overdue {
			where = append(where, "t.due_at<"+add(q.now()))
		}
		if len(intent.terms) > 0 {
			terms := []string{}
			for _, term := range intent.terms {
				terms = append(terms, "\""+strings.ReplaceAll(term, "\"", "")+"\"")
			}
			p := add(strings.Join(terms, " OR "))
			match := searchClause(table, p)
			if !tasks {
				match += " OR EXISTS(SELECT 1 FROM attachments a WHERE a.event_id=e.id AND " + searchClause("a", p) + ")"
			}
			where = append(where, "("+match+")")
		}
		if len(intent.entity) > 0 {
			conditions := []string{}
			for _, term := range intent.entity {
				p := add(term)
				haystack := "coalesce(e.author,'')||e.participants::text||e.semantic_index"
				if tasks {
					if delegations {
						haystack += "||t.assignee_name||t.assignee_email"
					}
					haystack += "||coalesce(t.title,'')||coalesce(t.description,'')||coalesce(t.evidence,'')"
				}
				conditions = append(conditions, "strpos(lower("+haystack+"),"+p+")>0")
			}
			where = append(where, "("+strings.Join(conditions, " OR ")+")")
		}
		return q.rows(query+" WHERE "+strings.Join(where, " AND ")+" ORDER BY "+order+fmt.Sprintf(" LIMIT %d", limit), args...)
	}
	events, tasks := []M{}, []M{}
	if intent.scope != "tasks" && intent.scope != "delegations" {
		events = retrieve(false, false)
	}
	if intent.scope != "events" {
		if intent.scope != "delegations" {
			tasks = retrieve(true, false)
		}
		if intent.scope != "tasks" {
			tasks = append(tasks, retrieve(true, true)...)
		}
	}
	return events, tasks
}
