package server

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

func dataItems(payload M) []M {
	var value any = payload
	if data, ok := payload["data"]; ok {
		value = data
	}
	if m, ok := value.(map[string]any); ok {
		value = m["items"]
	}
	if m, ok := value.(M); ok {
		value = m["items"]
	}
	out := []M{}
	if items, ok := value.([]any); ok {
		for _, v := range items {
			if item, ok := v.(map[string]any); ok {
				out = append(out, item)
			}
		}
	}
	return out
}
func (q *request) mtsJSON(source M, path string, query url.Values) M {
	access, refresh := q.sourceTokens(source)
	access = strings.TrimSpace(access)
	if strings.HasPrefix(strings.ToLower(access), "bearer ") {
		access = strings.TrimSpace(access[7:])
	}
	base := strings.TrimRight(str(obj(source, "settings"), "base_url"), "/")
	endpoint := base + path
	if len(query) > 0 {
		endpoint += "?" + query.Encode()
	}
	get := func(token string) (any, int, error) {
		return requestJSONValue(q.Context, "GET", endpoint, nil, map[string]string{"Authorization": "Bearer " + token, "Cookie": "access=" + token, "User-Agent": "Improver MTS-Link transcript connector"})
	}
	result, status, e := get(access)
	if (status == 401 || status == 403) && refresh != "" {
		var rotated M
		_, err := q.server.job(q.Context, func(rotation *request) bool {
			row := rotation.one("SELECT * FROM communication_sources WHERE id=$1 FOR UPDATE", source["id"])
			if row["source_type"] != "mts_link" || obj(row, "settings")["base_url"] != obj(source, "settings")["base_url"] {
				panic(fmt.Errorf("source changed"))
			}
			current, currentRefresh := rotation.sourceTokens(row)
			rotated = M{"access_token": current, "refresh_token": currentRefresh}
			if current == access {
				rotated = must(tokenExchange(q.Context, base, "Refresh", M{"refreshToken": currentRefresh}))
				encrypted := must(q.server.Config.Encrypt(tokenPrefix + string(must(json.Marshal(rotated)))))
				rotation.update("communication_sources", row["id"], M{"credential_encrypted": encrypted})
				source["credential_encrypted"] = encrypted
			} else {
				source["credential_encrypted"] = row["credential_encrypted"]
			}
			return true
		})
		if err != nil {
			panic(&sourceFailure{message: "Не удалось обновить авторизацию МТС Линк. Повторите SSO-вход в настройках источника."})
		}
		result, status, e = get(str(rotated, "access_token"))
	}
	if e != nil {
		switch {
		case status == 401 || status == 403:
			panic(&sourceFailure{message: "МТС Линк отклонил авторизацию. Выполните SSO-вход в настройках источника.", httpStatus: status})
		case status >= 300:
			panic(&sourceFailure{message: fmt.Sprintf("МТС Линк вернул HTTP %d при загрузке данных; будет повторено.", status), httpStatus: status})
		case status == 0:
			panic(&sourceFailure{message: "Не удалось связаться с МТС Линк; проверьте доступность сервиса. Будет повторено."})
		default:
			panic(&sourceFailure{message: "МТС Линк вернул некорректный JSON при загрузке данных; будет повторено."})
		}
	}
	// List endpoints may return a bare array, while profile/details and some
	// list endpoints return objects. SSO responses still require an object.
	switch value := result.(type) {
	case map[string]any:
		return M(value)
	case []any:
		if path != "/api/login" && !strings.HasSuffix(path, "/details") {
			return M{"data": value}
		}
	}
	panic(&sourceFailure{message: "МТС Линк вернул неподдерживаемый формат данных; будет повторено."})
}

// A listed transcript can be unpublished or removed before its content is
// available. Retry it on a later sync without blocking all other meetings.
func (q *request) mtsTranscript(source M, id string) (details M, utterances []M, available bool) {
	defer func() {
		if err := recover(); err != nil {
			if failure, ok := err.(*sourceFailure); ok && (failure.httpStatus == 404 || failure.httpStatus == 410) {
				slog.Info("MTS transcript unavailable; will check on next sync", "http_status", failure.httpStatus)
				available = false
				return
			}
			panic(err)
		}
	}()
	path := "/api/transcript/" + url.PathEscape(id)
	details = obj(q.mtsJSON(source, path+"/details", nil), "data")
	utterances = dataItems(q.mtsJSON(source, path, url.Values{"perPage": {"10000"}}))
	return details, utterances, true
}
func mtsURLs(values ...string) []string {
	out := []string{}
	seen := map[string]bool{}
	re := regexp.MustCompile(`(?i)https?://(?:[a-z0-9-]+\.)*mts-link\.ru/[^\s<>"']*`)
	for _, value := range values {
		for _, match := range re.FindAllString(value, -1) {
			match = strings.TrimRight(match, ".,;:!?)]}>")
			if !seen[strings.ToLower(match)] {
				seen[strings.ToLower(match)] = true
				out = append(out, match)
			}
		}
	}
	return out
}
func mtsKeys(values []string, known ...string) []string {
	set := map[string]bool{}
	for _, id := range known {
		if id != "" {
			set["id:"+strings.ToLower(id)] = true
		}
	}
	for _, link := range mtsURLs(values...) {
		u, e := url.Parse(link)
		if e != nil {
			continue
		}
		query := u.Query()
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		valid := (len(parts) == 1 && regexp.MustCompile(`^\d+$`).MatchString(parts[0])) || ((len(parts) == 2 || len(parts) == 3) && strings.EqualFold(parts[0], "j")) || (len(parts) == 2 && regexp.MustCompile(`^(\d+|join|meetings|events|room)$`).MatchString(strings.ToLower(parts[0]))) || (len(parts) == 5 && strings.EqualFold(parts[0], "j") && strings.EqualFold(parts[3], "session"))
		if valid {
			set["id:"+strings.ToLower(parts[len(parts)-1])] = true
			u.RawQuery = ""
			u.Fragment = ""
			set["url:"+strings.ToLower(strings.TrimRight(u.String(), "/"))] = true
		}
		for key, values := range query {
			switch strings.ToLower(key) {
			case "eventid", "eventsessionid", "activitysessionid", "meetingid", "roomid":
				for _, v := range values {
					if v != "" {
						set["id:"+strings.ToLower(v)] = true
					}
				}
			}
		}
	}
	out := []string{}
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func findPayloadURL(value any) string {
	switch v := value.(type) {
	case string:
		for _, link := range mtsURLs(v) {
			if len(mtsKeys([]string{link})) > 0 {
				return link
			}
		}
	case map[string]any:
		for _, key := range []string{"link", "url", "joinLink", "eventUrl", "roomUrl"} {
			if result := findPayloadURL(v[key]); result != "" {
				return result
			}
		}
		for _, child := range v {
			if result := findPayloadURL(child); result != "" {
				return result
			}
		}
	case []any:
		for _, child := range v {
			if result := findPayloadURL(child); result != "" {
				return result
			}
		}
	}
	return ""
}
func (q *request) mtsSync(source M, testOnly bool) int {
	if testOnly {
		q.mtsJSON(source, "/api/login", nil)
		return 0
	}
	now := time.Now().UTC()
	sessions := []M{}
	seen := map[string]bool{}
	for _, path := range []string{"/api/eventsessions/schedule", "/api/eventsessions/endless"} {
		previous := ""
		for page := 1; page <= 100; page++ {
			query := url.Values{"page": {strconv.Itoa(page)}, "perPage": {"100"}}
			if strings.HasSuffix(path, "schedule") {
				query.Set("eventType[0]", "meeting")
				query.Set("eventType[1]", "webinar")
				query.Set("eventType[2]", "training")
				query.Set("from", now.AddDate(0, 0, -int(num(obj(q.settings(), "communication_sources"), "initial_sync_days"))).Format(time.RFC3339))
				query.Set("to", now.Format(time.RFC3339))
			} else {
				query.Set("filters[visibility][eq]", "visible")
			}
			items := dataItems(q.mtsJSON(source, path, query))
			signature := hash(items)
			if len(items) == 0 || signature == previous {
				break
			}
			previous = signature
			for _, entry := range items {
				item := entry
				if nested := obj(entry, "data"); len(nested) > 0 {
					item = nested
				} else if nested := obj(entry, "eventSession"); len(nested) > 0 {
					item = nested
				}
				id := fmt.Sprint(item["id"])
				if item["id"] == nil {
					continue
				}
				activity := fmt.Sprint(item["activitySessionId"])
				key := id + ":" + activity
				if !seen[key] {
					seen[key] = true
					item = copyMap(item)
					item["_entry"] = entry
					sessions = append(sessions, item)
				}
			}
			if len(items) < 100 {
				break
			}
		}
	}
	count := 0
	for start := 0; start < len(sessions); start += 30 {
		batch := sessions[start:min(start+30, len(sessions))]
		query := url.Values{}
		for i, item := range batch {
			query.Set(fmt.Sprintf("items[%d][eventSessionId]", i), fmt.Sprint(item["id"]))
			if item["activitySessionId"] != nil {
				query.Set(fmt.Sprintf("items[%d][activitySessionId]", i), fmt.Sprint(item["activitySessionId"]))
			}
		}
		states := dataItems(q.mtsJSON(source, "/api/event-sessions/activity-sessions/transcript-states", query))
		for _, state := range states {
			if state["transcriptId"] == nil || boolean(state, "isDisabled") {
				continue
			}
			var session M
			for _, item := range batch {
				if fmt.Sprint(item["id"]) == fmt.Sprint(state["eventSessionId"]) && (item["activitySessionId"] == nil || fmt.Sprint(item["activitySessionId"]) == fmt.Sprint(state["activitySessionId"])) {
					session = item
					break
				}
			}
			if session == nil {
				continue
			}
			transcriptID := strings.TrimSpace(fmt.Sprint(state["transcriptId"]))
			if transcriptID == "" || transcriptID == "0" {
				continue
			}
			external := "transcript:" + transcriptID
			if len(q.rows("SELECT id FROM communication_events WHERE source_id=$1 AND external_id=$2", source["id"], external)) > 0 {
				continue
			}
			details, utterances, available := q.mtsTranscript(source, transcriptID)
			if !available {
				continue
			}
			starts, ends := timestamp(session["startsAt"]), timestamp(session["endsAt"])
			basis := "session"
			if starts == nil || ends == nil || !ends.After(*starts) {
				starts, ends = nil, nil
				if !boolean(state, "isPublished") || session["activitySessionId"] == nil || fmt.Sprint(session["activitySessionId"]) != fmt.Sprint(state["activitySessionId"]) {
					continue
				}
				for _, u := range utterances {
					if t := timestamp(u["dateTime"]); t != nil {
						if starts == nil || t.Before(*starts) {
							starts = t
						}
						if ends == nil || t.After(*ends) {
							ends = t
						}
					}
				}
				basis = "transcript"
			}
			if starts == nil || ends == nil || !ends.After(*starts) {
				continue
			}
			lines := []string{}
			participants := []M{}
			speakers := map[string]bool{}
			for _, u := range utterances {
				text := clean(str(u, "text"))
				if text == "" {
					continue
				}
				speaker := clean(str(u, "nickname"))
				if speaker == "" {
					speaker = "Участник"
				}
				prefix := speaker
				if t := timestamp(u["dateTime"]); t != nil {
					prefix = t.Format("15:04:05") + " · " + speaker
				}
				lines = append(lines, prefix+"\n"+text)
				key := speaker
				participant := M{"name": speaker, "role": "speaker"}
				if u["userId"] != nil {
					key = fmt.Sprint(u["userId"])
					participant["external_id"] = key
				}
				if !speakers[key] && len(participants) < 200 {
					speakers[key] = true
					participants = append(participants, participant)
				}
			}
			body := strings.Join(lines, "\n\n")
			if body == "" {
				continue
			}
			link := findPayloadURL(map[string]any(session))
			title := str(details, "transcriptName")
			if title == "" {
				title = str(session, "name")
			}
			if title == "" {
				title = "Результаты встречи"
			}
			created := timestamp(details["createdAt"])
			if created == nil {
				created = ends
			}
			eventSession := fmt.Sprint(session["id"])
			activity := ""
			if session["activitySessionId"] != nil {
				activity = fmt.Sprint(session["activitySessionId"])
			}
			keys := q.mtsReferenceKeys([]string{link}, eventSession, activity)
			header := M{"transcript_id": transcriptID, "event_session_id": eventSession, "activity_session_id": activity, "status": state["status"], "visibility": state["visibility"], "is_published": state["isPublished"], "structured_utterances_only": true, "time_basis": basis}
			event := q.insert("communication_events", M{"source_id": source["id"], "source_type": "mts_link", "external_id": external, "event_type": "meeting_transcript", "direction": "INCOMING", "thread_external_id": "mts-link:" + eventSession, "subject": bounded(title, 500), "author": details["ownerName"], "participants": participants, "occurred_at": *created, "body": body, "source_url": link, "raw_headers": M{"MTS-Link": header}, "content_hash": fmt.Sprintf("%x", sha256.Sum256([]byte(body)))})
			status := str(state, "status")
			if status == "" {
				status = "ready"
			}
			q.insert("meeting_results", M{"source_id": source["id"], "transcript_id": transcriptID, "event_session_id": eventSession, "activity_session_id": activity, "source_event_id": event["id"], "origin_type": "mts_transcript", "title": bounded(title, 500), "starts_at": *starts, "ends_at": *ends, "owner_name": details["ownerName"], "meeting_url": link, "mts_link_keys": keys, "transcript_status": bounded(status, 64)})
			count++
		}
	}
	return count
}

func (q *request) mtsReferenceKeys(values []string, known ...string) []string {
	set := setOf(mtsKeys(nil, known...))
	sources := q.rows("SELECT settings FROM communication_sources WHERE enabled AND source_type='mts_link'")
	for _, link := range mtsURLs(values...) {
		ids := stringSet{}
		matched := false
		if len(link) <= 4096 {
			for _, source := range sources {
				rules := stringsArray(obj(source, "settings")["link_patterns"])
				if obj(source, "settings")["link_patterns"] == nil {
					rules = []string{mtsPattern}
				}
				for _, pattern := range rules {
					re, err := compileLinkPattern(pattern)
					if err != nil {
						continue
					}
					m, err := re.FindStringMatch(link)
					if err != nil || m == nil || m.String() != link {
						continue
					}
					group := m.GroupByName("meeting_id")
					if group == nil {
						continue
					}
					id := group.String()
					if !regexp.MustCompile(`^[\pL\pN_.-]{1,128}$`).MatchString(id) {
						continue
					}
					ids[strings.ToLower(id)] = true
					matched = true
					break
				}
			}
		}
		if matched {
			if len(ids) == 1 {
				set["id:"+ids.sorted()[0]] = true
			}
		} else {
			for _, key := range mtsKeys([]string{link}) {
				set[key] = true
			}
		}
	}
	return set.sorted()
}
func mtsJoinURL(link string) string {
	u, e := url.Parse(link)
	if e != nil {
		return link
	}
	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	if len(parts) == 5 && strings.EqualFold(parts[0], "j") && strings.EqualFold(parts[3], "session") && regexp.MustCompile(`^\d+$`).MatchString(parts[2]) && regexp.MustCompile(`^\d+$`).MatchString(parts[4]) {
		u.Path = "/" + strings.Join(parts[:3], "/")
		u.RawPath = ""
		u.Fragment = ""
		return u.String()
	}
	return link
}
