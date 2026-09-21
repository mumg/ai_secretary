package server

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/mumg/ai_secretary/backend/internal/config"
)

// PreparePreconfiguration creates only FK identities. File values remain in
// process memory and are supplied to each read as a bound JSON parameter.
func (s *Server) PreparePreconfiguration(ctx context.Context) error {
	if err := validateFileRelationships(s.Config.Preconfiguration.Settings); err != nil {
		return err
	}
	for _, source := range s.Config.Preconfiguration.Sources {
		// Reuse validation of patterns/relationships without touching the database.
		if err := validateFileValues(source.Settings); err != nil {
			return err
		}
	}
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	for _, source := range s.Config.Preconfiguration.Sources {
		_, err = tx.Exec(ctx, `INSERT INTO communication_sources (id,label,source_type,enabled,settings,preconfigured) VALUES ($1,NULL,NULL,NULL,'{}',true) ON CONFLICT (id) DO NOTHING`, source.ID)
		if err != nil {
			return err
		}
		// A new endpoint needs a fresh synchronization cursor. Persist only a
		// fingerprint, never the baseline connection fields themselves.
		fingerprint := hash([]any{source.SourceType, source.Settings, source.Enabled})
		var previous string
		if err = tx.QueryRow(ctx, "SELECT configuration_fingerprint FROM communication_sources WHERE id=$1 FOR UPDATE", source.ID).Scan(&previous); err != nil {
			return err
		}
		if previous != fingerprint {
			if _, err = tx.Exec(ctx, "DELETE FROM source_cursors WHERE source_id=$1 AND EXISTS (SELECT 1 FROM communication_sources WHERE id=$1 AND preconfigured)", source.ID); err != nil {
				return err
			}
			if _, err = tx.Exec(ctx, "UPDATE communication_sources SET configuration_fingerprint=$2 WHERE id=$1 AND preconfigured", source.ID, fingerprint); err != nil {
				return err
			}
		}
	}
	return tx.Commit(ctx)
}
func validateFileValues(settings config.Object) (err error) {
	defer func() {
		if recover() != nil {
			err = fmt.Errorf("invalid preconfiguration link patterns")
		}
	}()
	if patterns, ok := settings["link_patterns"]; ok {
		validatePatterns(patterns)
	}
	return nil
}

var sourceRelation = regexp.MustCompile(`\bcommunication_sources\b`)

// Resolve the relation before SQL filtering, sorting and joins so API, worker,
// search and status use exactly the same effective configuration.
func (q *request) sourceQuery(sql string, args []any) (string, []any) {
	if !sourceRelation.MatchString(sql) {
		return sql, args
	}
	data := must(json.Marshal(q.server.Config.SourceDefaults()))
	args = append(args, string(data))
	parameter := fmt.Sprintf("$%d::jsonb", len(args))
	base := parameter + "->s.id"
	field := func(name string) string { return "(" + base + ")->>'" + name + "'" }
	lock := ""
	if strings.HasSuffix(sql, " FOR UPDATE") {
		sql = strings.TrimSuffix(sql, " FOR UPDATE")
		if !strings.Contains(sql, "WHERE id=$1") || len(args) < 2 {
			panic("unsupported source locking query")
		}
		lock = " AND s.id=$1 FOR UPDATE OF s"
	}
	relation := `communication_sources AS (
 SELECT s.id,
 CASE WHEN s.preconfigured THEN COALESCE(s.label,` + field("label") + `,s.id) ELSE s.label END AS label,
 CASE WHEN s.preconfigured THEN COALESCE(s.source_type,` + field("source_type") + `,'external_tasks') ELSE s.source_type END AS source_type,
 CASE WHEN s.preconfigured THEN (` + base + `) IS NOT NULL AND COALESCE(s.enabled,(` + field("enabled") + `)::boolean,false) ELSE s.enabled END AS enabled,
 CASE WHEN s.preconfigured AND (s.source_type IS NULL OR s.source_type=` + field("source_type") + `) THEN COALESCE((` + base + `)->'settings','{}'::jsonb) || s.settings::jsonb ELSE s.settings::jsonb END AS settings,
 s.credential_encrypted,s.last_sync_at,s.last_error,s.created_at,s.updated_at,s.preconfigured,s.configuration_deleted
 FROM communication_sources s WHERE NOT s.configuration_deleted` + lock + `)
 `
	if strings.HasPrefix(sql, "WITH ") {
		return "WITH " + relation + "," + strings.TrimPrefix(sql, "WITH "), args
	}
	return "WITH " + relation + sql, args
}
func (q *request) sourceOverrides(id any, values M) M {
	stored := q.db.QueryRow(q.Context, "SELECT preconfigured FROM communication_sources WHERE id=$1", id)
	var configured bool
	check(stored.Scan(&configured))
	if !configured {
		return values
	}
	base := config.Section(q.server.Config.SourceDefaults(), fmt.Sprint(id))
	result := M{}
	for key, value := range values {
		switch key {
		case "label", "source_type", "enabled":
			if hash(value) == hash(base[key]) {
				result[key] = nil
			} else {
				result[key] = value
			}
		case "settings":
			settingsBase := config.Section(base, key)
			if kind, ok := values["source_type"]; ok && kind != base["source_type"] {
				settingsBase = config.Object{}
			}
			result[key] = config.Difference(settingsBase, config.Object(obj(values, key)))
		default:
			result[key] = value
		}
	}
	return result
}

func validateFileRelationships(settings config.Object) (err error) {
	defer func() {
		if recover() != nil {
			err = fmt.Errorf("invalid preconfiguration relationships")
		}
	}()
	validateRelationships(M(config.Section(settings, "relationships")))
	return nil
}
func (q *request) fileSourceCredential(row M) string {
	if !boolean(row, "preconfigured") {
		return ""
	}
	for _, source := range q.server.Config.Preconfiguration.Sources {
		if source.ID != str(row, "id") || source.SourceType != str(row, "source_type") {
			continue
		}
		// Never forward a file credential to a user-selected replacement endpoint.
		for _, key := range []string{"host", "port", "tls", "ews_url", "base_url"} {
			if hash(source.Settings[key]) != hash(obj(row, "settings")[key]) {
				return ""
			}
		}
		return source.Credential
	}
	return ""
}
