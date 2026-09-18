package server

import (
	"encoding/json"
	"github.com/dlclark/regexp2"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/config"
	"github.com/mumg/ai_secretary/backend/internal/store"
	"golang.org/x/text/cases"
)

const mtsPattern = `^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d+)(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$`

var sourceID = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)

func newID() string { return store.UUID() }
func (q *request) sourceRead(row M, external bool) M {
	row["tags"] = q.rows("SELECT t.id,t.name FROM tags t JOIN communication_source_tags st ON st.tag_id=t.id WHERE st.source_id=$1 ORDER BY t.name", row["id"])
	if external {
		return project("ExternalTaskSourceRead", row)
	}
	row["credential_configured"] = str(row, "credential_encrypted") != ""
	row["refresh_token_configured"] = false
	if str(row, "source_type") == "mts_link" && str(row, "credential_encrypted") != "" {
		plain := must(q.server.Config.Decrypt(str(row, "credential_encrypted")))
		row["refresh_token_configured"] = strings.HasPrefix(plain, "mts-link-tokens:v1:")
	}
	settings := obj(row, "settings")
	if _, ok := settings["link_patterns"]; !ok {
		settings["link_patterns"] = []string{}
		if row["source_type"] == "mts_link" {
			settings["link_patterns"] = []string{mtsPattern}
		}
	}
	row["settings"] = settings
	return project("SourceRead", row)
}
func (q *request) tags(source string, ids any) {
	a, ok := ids.([]any)
	if !ok {
		fail(422, "tag_ids must be an array")
	}
	seen := map[string]bool{}
	for _, id := range a {
		s, ok := id.(string)
		if !ok {
			fail(422, "Invalid tag ID")
		}
		if seen[s] {
			continue
		}
		seen[s] = true
		if len(q.rows("SELECT id FROM tags WHERE id::text=$1", s)) == 0 {
			fail(422, "One or more tags do not exist")
		}
	}
	q.exec("DELETE FROM communication_source_tags WHERE source_id=$1", source)
	for id := range seen {
		q.exec("INSERT INTO communication_source_tags (source_id,tag_id) VALUES ($1,$2)", source, id)
	}
}
func (q *request) saveSource(m M, existing M) M {
	textField(m, "id", 1, 128, true)
	id := str(m, "id")
	if !sourceID.MatchString(id) {
		fail(422, "Invalid source ID")
	}
	textField(m, "label", 1, 255, true)
	enum(m, "source_type", "imap", "exchange", "mts_link", "external_tasks")
	if str(m, "source_type") == "" {
		fail(422, "source_type is required")
	}
	if _, ok := m["enabled"]; !ok {
		m["enabled"] = true
	}
	if _, ok := m["enabled"].(bool); !ok {
		fail(422, "Invalid enabled")
	}
	fields := map[string][]string{"imap": {"host", "port", "tls", "username", "inbox_folder", "sent_folder"}, "exchange": {"ews_url", "primary_smtp_address", "username", "auth_type", "inbox_folder", "sent_folder"}, "mts_link": {"base_url", "poll_interval_seconds"}, "external_tasks": {}}
	settings := pick(obj(m, "settings"), append(fields[str(m, "source_type")], "link_patterns")...)
	if _, ok := settings["link_patterns"]; !ok {
		settings["link_patterns"] = []any{}
		if m["source_type"] == "mts_link" {
			settings["link_patterns"] = []any{mtsPattern}
		}
		if existing != nil && existing["source_type"] == m["source_type"] {
			if v, ok := obj(existing, "settings")["link_patterns"]; ok {
				settings["link_patterns"] = v
			}
		}
	}
	validatePatterns(settings["link_patterns"])
	credential := str(m, "credential")
	if credential == "" && existing != nil {
		credential = str(existing, "credential_encrypted")
	}
	if boolean(m, "enabled") {
		required := map[string][]string{"imap": {"host", "port", "username"}, "exchange": {"ews_url", "primary_smtp_address", "username"}, "mts_link": {"base_url"}}
		for _, k := range required[str(m, "source_type")] {
			if settings[k] == nil || settings[k] == "" {
				fail(422, "Missing source connection settings")
			}
		}
		if m["source_type"] != "external_tasks" && credential == "" {
			fail(422, "Source credential is required")
		}
	}
	if v, ok := settings["base_url"]; ok {
		u, e := url.Parse(str(settings, "base_url"))
		if e != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
			fail(422, "Invalid source base_url")
		}
		settings["base_url"] = strings.TrimRight(v.(string), "/")
	}
	if existing != nil && existing["source_type"] == "mts_link" && obj(existing, "settings")["base_url"] != settings["base_url"] && str(m, "credential") == "" && credential != "" {
		fail(422, "Для смены шлюза укажите новый access token")
	}
	v := pick(m, "id", "label", "source_type", "enabled")
	v["settings"] = settings
	v["last_error"] = nil
	if str(m, "credential") != "" {
		v["credential_encrypted"] = must(q.server.Config.Encrypt(str(m, "credential")))
	}
	var row M
	if existing == nil {
		row = q.insert("communication_sources", v)
	} else {
		delete(v, "id")
		row = q.update("communication_sources", id, v)
		before, _ := json.Marshal(existing["settings"])
		after, _ := json.Marshal(settings)
		if string(before) != string(after) || existing["source_type"] != m["source_type"] || (!boolean(existing, "enabled") && boolean(m, "enabled")) {
			q.exec("DELETE FROM source_cursors WHERE source_id=$1", id)
		}
	}
	if ids, ok := m["tag_ids"]; ok && ids != nil {
		q.tags(id, ids)
	}
	return q.sourceRead(row, false)
}
func validatePatterns(value any) {
	a, ok := value.([]any)
	if !ok {
		fail(422, "link_patterns must be an array")
	}
	if len(a) > 20 {
		fail(422, "Too many link patterns")
	}
	for _, v := range a {
		s, ok := v.(string)
		if !ok || len(s) == 0 || len(s) > 1024 {
			fail(422, "Invalid link pattern")
		}
		r, e := compileLinkPattern(s)
		if e != nil || r.GroupNumberFromName("meeting_id") < 0 {
			fail(422, "В правиле ссылки нужна именованная группа (?P<meeting_id>...)")
		}
	}
}
func (q *request) settingsRead() M {
	settings := q.settings()
	rows := q.rows("SELECT * FROM system_settings WHERE id=1")
	firebase, key := false, false
	if len(rows) > 0 {
		firebase = str(rows[0], "firebase_credentials_encrypted") != ""
		key = str(obj(rows[0], "payload"), "llm_api_key_encrypted") != ""
	}
	return M{"settings": settings, "local_web_only": q.server.Config.LocalOnly, "firebase_configured": firebase, "llm_api_key_configured": key, "filter_reconciliation": nil, "identity_requeued": nil}
}
func (s *Server) adminRoutes() {
	s.route("GET /api/v1/admin/settings", false, func(q *request) any { return q.settingsRead() })
	s.route("PUT /api/v1/admin/settings", true, func(q *request) any {
		m := q.body()
		if _, ok := m["settings"].(map[string]any); !ok {
			fail(422, "settings must be an object")
		}
		previous := q.settings()
		p := config.Merge(config.Object(q.settings()), config.Object(obj(m, "settings")))
		if e := config.Validate(p); e != nil {
			fail(422, e.Error())
		}
		for _, pair := range [][2]string{{"server", "data_dir"}, {"server", "local_web_only"}, {"llm", "api_key"}, {"document_parser", "base_url"}, {"identity", "addresses"}, {"communication_sources", "items"}, {"notifications", "firebase_credentials"}} {
			delete(config.Section(p, pair[0]), pair[1])
		}
		rows := q.rows("SELECT * FROM system_settings WHERE id=1 FOR UPDATE")
		v := M{"payload": p}
		if len(rows) > 0 {
			if key := str(obj(rows[0], "payload"), "llm_api_key_encrypted"); key != "" {
				p["llm_api_key_encrypted"] = key
			}
		}
		key := strings.TrimSpace(str(m, "llm_api_key"))
		if key != "" && boolean(m, "clear_llm_api_key") {
			fail(422, "Нельзя одновременно заменить и удалить API_KEY")
		}
		if key != "" {
			if len(key) > 8192 {
				fail(422, "Invalid API_KEY")
			}
			for _, c := range key {
				if c < 33 || c > 126 {
					fail(422, "Invalid API_KEY")
				}
			}
			p["llm_api_key_encrypted"] = must(q.server.Config.Encrypt(key))
		}
		if boolean(m, "clear_llm_api_key") {
			delete(p, "llm_api_key_encrypted")
		}
		if firebase := str(m, "firebase_credentials_json"); firebase != "" {
			var account M
			if json.Unmarshal([]byte(firebase), &account) != nil || str(account, "project_id") == "" {
				fail(422, "Firebase service account JSON is invalid")
			}
			v["firebase_credentials_encrypted"] = must(q.server.Config.Encrypt(firebase))
		}
		if len(rows) == 0 {
			v["id"] = 1
			q.insert("system_settings", v)
		} else {
			q.update("system_settings", 1, v)
		}
		result := q.settingsRead()
		if hash(previous["analysis_filters"]) != hash(p["analysis_filters"]) {
			result["filter_reconciliation"] = q.reconcileFilters()
		}
		if hash(previous["identity"]) != hash(p["identity"]) {
			result["identity_requeued"] = q.requeueIdentity()
		}
		return result
	})
	s.route("GET /api/v1/admin/sources", false, func(q *request) any {
		out := []M{}
		for _, r := range q.rows("SELECT * FROM communication_sources ORDER BY label") {
			out = append(out, q.sourceRead(r, false))
		}
		return out
	})
	s.route("POST /api/v1/admin/sources", true, func(q *request) any { q.status = 201; return q.saveSource(q.body(), nil) })
	s.route("PUT /api/v1/admin/sources/{source}", true, func(q *request) any {
		id := q.r.PathValue("source")
		row := q.get("communication_sources", id)
		m := q.body()
		if m["id"] != id {
			fail(409, "Source ID cannot be changed")
		}
		return q.saveSource(m, row)
	})
	s.route("DELETE /api/v1/admin/sources/{source}", true, func(q *request) any {
		id := q.r.PathValue("source")
		q.get("communication_sources", id)
		q.exec("DELETE FROM communication_sources WHERE id=$1", id)
		q.exec("DELETE FROM daily_plans")
		q.status = 204
		return nil
	})
	s.route("GET /api/v1/admin/tags", false, func(q *request) any {
		return q.rows("SELECT t.id,t.name,t.created_at,t.updated_at,(SELECT count(*) FROM communication_source_tags st WHERE st.tag_id=t.id) AS source_count FROM tags t ORDER BY name")
	})
	for _, method := range []string{"POST", "PUT"} {
		path := "/api/v1/admin/tags"
		if method == "PUT" {
			path += "/{id}"
		}
		s.route(method+" "+path, true, func(q *request) any {
			m := q.body()
			m["name"] = clean(str(m, "name"))
			textField(m, "name", 1, 100, true)
			v := M{"name": m["name"], "normalized_name": cases.Fold().String(str(m, "name"))}
			var row M
			if method == "POST" {
				q.status = 201
				row = q.insert("tags", v)
			} else {
				id := q.id("id")
				q.get("tags", id)
				row = q.update("tags", id, v)
			}
			row["source_count"] = q.one("SELECT count(*) AS n FROM communication_source_tags WHERE tag_id=$1", row["id"])["n"]
			return project("TagRead", row)
		})
	}
	s.route("DELETE /api/v1/admin/tags/{id}", true, func(q *request) any {
		id := q.id("id")
		q.get("tags", id)
		q.exec("DELETE FROM tags WHERE id=$1", id)
		q.status = 204
		return nil
	})
	s.route("POST /api/v1/admin/source-link-preview", false, func(q *request) any {
		m := q.body()
		validatePatterns(m["patterns"])
		matches := []M{}
		u, e := url.Parse(str(m, "url"))
		if e == nil && u.User == nil && u.Hostname() != "" && (u.Scheme == "http" || u.Scheme == "https") {
			for _, pattern := range stringsArray(m["patterns"]) {
				re := must(compileLinkPattern(pattern))
				match, err := re.FindStringMatch(str(m, "url"))
				if err == nil && match != nil && match.String() == str(m, "url") {
					id := match.GroupByName("meeting_id").String()
					if regexp.MustCompile(`^[\pL\pN_.-]{1,128}$`).MatchString(id) {
						matches = append(matches, M{"source_id": m["source_id"], "source_type": m["source_type"], "meeting_id": id})
						break
					}
				}
			}
		}
		return M{"matches": matches}
	})
}

func compileLinkPattern(pattern string) (*regexp2.Regexp, error) {
	r, e := regexp2.Compile(strings.ReplaceAll(pattern, "(?P<", "(?<"), 0)
	if e == nil {
		r.MatchTimeout = 20 * time.Millisecond
	}
	return r, e
}
