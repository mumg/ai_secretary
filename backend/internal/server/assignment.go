package server

import (
	"encoding/json"
	"regexp"
	"sort"
	"strings"
)

func cleanEmail(body string) string {
	lines := []string{}
	boundary := regexp.MustCompile(`(?i)^(?:On .+ wrote:|От:\s.+|From:\s.+|[-_]{2,}\s*(?:Original Message|Исходное сообщение))$`)
	for _, line := range strings.Split(strings.ReplaceAll(body, "\x00", ""), "\n") {
		trim := strings.TrimSpace(line)
		if trim == "--" || boundary.MatchString(trim) {
			break
		}
		if !strings.HasPrefix(trim, ">") {
			lines = append(lines, strings.TrimRight(line, " \t\r"))
		}
	}
	return strings.TrimSpace(regexp.MustCompile(`\n{3,}`).ReplaceAllString(strings.Join(lines, "\n"), "\n\n"))
}
func normalizedQuote(s string) string { return normalizeName(strings.ReplaceAll(s, "**", "")) }
func nameMatches(short, full string) bool {
	tokenize := func(s string) []string { return regexp.MustCompile(`[\pL\pN_-]+`).FindAllString(normalizeName(s), -1) }
	supplied, available := tokenize(short), tokenize(full)
	sort.SliceStable(supplied, func(i, j int) bool { return len([]rune(supplied[i])) > len([]rune(supplied[j])) })
	for _, token := range supplied {
		found := -1
		for i, name := range available {
			if name == token || (len([]rune(token)) == 1 && strings.HasPrefix(name, token)) {
				found = i
				break
			}
		}
		if found < 0 {
			return false
		}
		available = append(available[:found], available[found+1:]...)
	}
	return len(supplied) > 0
}
func roster(event M) []M {
	var people []M
	_ = json.Unmarshal(must(json.Marshal(event["participants"])), &people)
	return people
}
func ownerIdentity(owner string, names, addresses []string, people []M) string {
	matches := patterns["assignment__EMAIL"].FindStringMatch
	mailbox, _ := matches(strings.ToLower(owner))
	if mailbox != nil {
		for m := mailbox; m != nil; {
			for _, a := range addresses {
				if strings.EqualFold(m.String(), a) {
					return "user"
				}
			}
			m, _ = patterns["assignment__EMAIL"].FindNextMatch(m)
		}
		return "other"
	}
	userNames := append([]string{}, names...)
	own := func(p M) bool {
		for _, a := range addresses {
			if strings.EqualFold(str(p, "address"), a) || strings.EqualFold(str(p, "email"), a) {
				return true
			}
		}
		return false
	}
	for _, p := range people {
		if own(p) {
			userNames = append(userNames, str(p, "name"))
		}
	}
	name := strings.Trim(owner, "@ ")
	userMatch := false
	for _, full := range userNames {
		userMatch = userMatch || nameMatches(name, full)
	}
	competitors := false
	speakers := map[string]bool{}
	for _, p := range people {
		if nameMatches(name, str(p, "name")) {
			if !personIsUser(p, names, setOf(addresses)) {
				competitors = true
			}
			if id := str(p, "external_id"); id != "" {
				speakers[id] = true
			}
		}
	}
	if userMatch {
		if competitors || len(speakers) > 1 {
			return "uncertain"
		}
		return "user"
	}
	supplied := regexp.MustCompile(`[\pL\pN_-]+`).FindAllString(normalizeName(name), -1)
	fullProfile := false
	for _, full := range userNames {
		known := regexp.MustCompile(`[\pL\pN_-]+`).FindAllString(normalizeName(full), -1)
		fullProfile = fullProfile || len(known) > 1
		if len(known) <= 1 || len(known) >= len(supplied) {
			continue
		}
		fuller := false
		for _, other := range userNames {
			a, b := nameWords(full), nameWords(other)
			fuller = fuller || (len(a) < len(b) && a.subset(b))
		}
		if fuller {
			continue
		}
		remaining := append([]string{}, supplied...)
		matched := true
		for _, token := range known {
			found := -1
			for i, part := range remaining {
				if part == token || (len([]rune(part)) == 1 && strings.HasPrefix(token, part)) {
					found = i
					break
				}
			}
			if found < 0 {
				matched = false
				break
			}
			remaining = append(remaining[:found], remaining[found+1:]...)
		}
		if matched {
			return "uncertain"
		}
	}
	if fullProfile {
		return "other"
	}
	return "uncertain"
}
func taskOwner(event M, evidence string, names, addresses []string, previous ...M) string {
	body := str(event, "body")
	if event["event_type"] == "email" {
		body = cleanEmail(body)
	}
	quote := normalizedQuote(evidence)
	if len([]rune(quote)) >= 8 && !strings.Contains(normalizedQuote(body), quote) {
		originals := []string{str(event, "body")}
		for _, p := range previous {
			if p["occurred_at"] != nil {
				originals = append(originals, str(p, "body"))
			}
		}
		for _, original := range originals {
			if event["event_type"] == "email" && strings.Contains(normalizedQuote(original), quote) {
				return "stale"
			}
		}
		if strings.Contains(normalizedQuote(str(event, "body")), quote) {
			return "stale"
		}
	}
	labels := patterns["assignment__OWNER_LABEL"]
	if m, _ := labels.FindStringMatch(body); m == nil {
		return ""
	}
	if len([]rune(quote)) < 8 {
		return "uncertain"
	}
	blocks := []string{}
	for _, block := range regexp.MustCompile(`\n\s*\n|(?m)^\s*[*•⁃▪‣–—-]\s|(?m)^\s*\d+[.)]\s`).Split(strings.ReplaceAll(body, "**", ""), -1) {
		block = strings.TrimSpace(block)
		if block == "" {
			continue
		}
		if len(blocks) > 0 && matchPattern("assignment__OWNER_LABEL", block) != nil && matchPattern("assignment__OWNER_LABEL", block).Index == 0 {
			blocks[len(blocks)-1] += "\n" + block
		} else {
			blocks = append(blocks, block)
		}
	}
	matching := []string{}
	for _, b := range blocks {
		if strings.Contains(normalizedQuote(b), quote) {
			matching = append(matching, b)
		}
	}
	if len(matching) != 1 {
		return "uncertain"
	}
	label := matchPattern("assignment__OWNER_LABEL", matching[0])
	if label == nil {
		return ""
	}
	if next, _ := labels.FindNextMatch(label); next != nil {
		return "uncertain"
	}
	tail := clean(string([]rune(matching[0])[label.Index+label.Length:]))
	verdicts := []string{}
	for tail != "" {
		person := matchPattern("assignment__EMAIL", tail)
		if person == nil || person.Index != 0 {
			person = matchPattern("assignment__PERSON", tail)
		}
		if person == nil || person.Index != 0 {
			verdicts = append(verdicts, "uncertain")
			break
		}
		verdicts = append(verdicts, ownerIdentity(person.String(), names, addresses, assignmentRoster(event, previous)))
		tail = string([]rune(tail)[person.Length:])
		separator := regexp.MustCompile(`^\s*(?:,|/|;|и\s)\s*`).FindString(tail)
		if separator == "" {
			break
		}
		tail = strings.TrimPrefix(tail, separator)
		if rx(`(?i)^(?:срок|до\b|дедлайн|к\s+\d)`, tail) {
			break
		}
	}
	for _, v := range verdicts {
		if v == "user" {
			return "user"
		}
	}
	for _, v := range verdicts {
		if v == "uncertain" {
			return "uncertain"
		}
	}
	return "other"
}
func transcriptOwner(event M, quote, evidence string, names, addresses []string) string {
	needle := normalizedQuote(quote)
	if len([]rune(needle)) < 8 {
		return "unproven"
	}
	body := str(event, "body")
	headers := patterns["assignment__TRANSCRIPT_TURN"]
	verdicts := []string{}
	for header := matchPattern("assignment__TRANSCRIPT_TURN", body); header != nil; {
		next, _ := headers.FindNextMatch(header)
		end := len([]rune(body))
		if next != nil {
			end = next.Index
		}
		turn := string([]rune(body)[header.Index:end])
		if strings.Contains(normalizedQuote(turn), needle) && len([]rune(normalizedQuote(evidence))) >= 8 && (strings.Contains(normalizedQuote(turn), normalizedQuote(evidence)) || strings.Contains(normalizedQuote(evidence), needle)) {
			text, _ := headers.Replace(quote, "", -1, -1)
			verdict := "other"
			signals := assignmentSignals(event, names, addresses, nil)
			for _, name := range stringsArray(signals["unambiguous_names"]) {
				if hasName(text, name) {
					verdict = "user"
				}
			}
			for _, address := range addresses {
				if strings.Contains(strings.ToLower(text), address) {
					verdict = "user"
				}
			}
			if verdict != "user" {
				for _, name := range stringsArray(signals["ambiguous_names"]) {
					if hasName(text, name) {
						verdict = "uncertain"
					}
				}
			}
			if verdict == "other" && matchPattern("assignment__COMMITMENT", text) != nil && !rx(`(?i)\b(?:не|нет)\b`, text) {
				verdict = ownerIdentity(header.GroupByName("speaker").String(), names, addresses, roster(event))
			}
			verdicts = append(verdicts, verdict)
		}
		header = next
	}
	if len(verdicts) == 0 {
		return "unproven"
	}
	if len(verdicts) == 1 {
		return verdicts[0]
	}
	for _, v := range verdicts {
		if v != "other" {
			return "uncertain"
		}
	}
	return "other"
}

func assignmentRoster(event M, previous []M) []M {
	people := append(roster(event), authorPeople(str(event, "author"))...)
	for _, p := range previous {
		people = append(people, roster(p)...)
		if p["occurred_at"] != nil {
			people = append(people, authorPeople(str(p, "author"))...)
		}
	}
	return people
}
