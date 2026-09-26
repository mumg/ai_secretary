// Package store provides PostgreSQL access using the existing application schema.
package store

import (
	"context"
	"crypto/rand"
	"embed"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Record map[string]any

const LatestRevision = 32

type DB interface {
	Exec(context.Context, string, ...any) (pgconn.CommandTag, error)
	Query(context.Context, string, ...any) (pgx.Rows, error)
	QueryRow(context.Context, string, ...any) pgx.Row
}
type Column struct {
	Nullable      bool   `json:"nullable"`
	Type          string `json:"type"`
	Primary       bool   `json:"primary"`
	Default       any    `json:"default"`
	ServerDefault bool   `json:"server_default"`
}

//go:embed models.json migrations/*.sql
var assets embed.FS
var Models map[string]map[string]Column

func init() {
	b, _ := assets.ReadFile("models.json")
	if err := json.Unmarshal(b, &Models); err != nil {
		panic(err)
	}
}

func UUID() string {
	var b [16]byte
	if _, e := rand.Read(b[:]); e != nil {
		panic(e)
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[:4], b[4:6], b[6:8], b[8:10], b[10:])
}
func Quote(s string) string { return pgx.Identifier{s}.Sanitize() }
func Rows(ctx context.Context, db DB, sql string, args ...any) ([]Record, error) {
	rows, e := db.Query(ctx, "SELECT row_to_json(_row) FROM ("+sql+") _row", args...)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	result := []Record{}
	for rows.Next() {
		var b []byte
		if e = rows.Scan(&b); e != nil {
			return nil, e
		}
		var r Record
		if e = json.Unmarshal(b, &r); e != nil {
			return nil, e
		}
		result = append(result, r)
	}
	return result, rows.Err()
}
func One(ctx context.Context, db DB, sql string, args ...any) (Record, error) {
	r, e := Rows(ctx, db, sql, args...)
	if e != nil {
		return nil, e
	}
	if len(r) == 0 {
		return nil, pgx.ErrNoRows
	}
	return r[0], nil
}
func Get(ctx context.Context, db DB, table string, id any) (Record, error) {
	if Models[table] == nil {
		return nil, fmt.Errorf("unknown table")
	}
	return One(ctx, db, "SELECT * FROM "+Quote(table)+" WHERE id=$1", id)
}
func Insert(ctx context.Context, db DB, table string, values Record) (Record, error) {
	model, ok := Models[table]
	if !ok {
		return nil, fmt.Errorf("unknown table")
	}
	r := Record{}
	for k, v := range values {
		r[k] = v
	}
	for k, c := range model {
		if _, ok = r[k]; ok {
			continue
		}
		if c.Primary && c.Type == "CHAR(32)" {
			r[k] = UUID()
		} else if c.Default != nil {
			r[k] = c.Default
		}
	}
	keys := []string{}
	for k := range r {
		if _, ok = model[k]; !ok {
			return nil, fmt.Errorf("unknown column %s", k)
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	names := []string{}
	params := []string{}
	args := []any{}
	for i, k := range keys {
		names = append(names, Quote(k))
		params = append(params, fmt.Sprintf("$%d", i+1))
		v := r[k]
		if model[k].Type == "JSON" && v != nil {
			b, e := json.Marshal(v)
			if e != nil {
				return nil, e
			}
			v = json.RawMessage(b)
		}
		args = append(args, v)
	}
	sql := "INSERT INTO " + Quote(table) + " (" + strings.Join(names, ",") + ") VALUES (" + strings.Join(params, ",") + ") RETURNING *"
	return returning(ctx, db, sql, args...)
}
func Update(ctx context.Context, db DB, table string, id any, values Record) (Record, error) {
	model, ok := Models[table]
	if !ok {
		return nil, fmt.Errorf("unknown table")
	}
	keys := []string{}
	for k := range values {
		if _, ok = model[k]; !ok {
			return nil, fmt.Errorf("unknown column")
		}
		if k == "id" {
			return nil, fmt.Errorf("immutable id")
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	sets := []string{}
	args := []any{id}
	for _, k := range keys {
		v := values[k]
		if model[k].Type == "JSON" && v != nil {
			b, e := json.Marshal(v)
			if e != nil {
				return nil, e
			}
			v = json.RawMessage(b)
		}
		args = append(args, v)
		sets = append(sets, fmt.Sprintf("%s=$%d", Quote(k), len(args)))
	}
	if _, ok = model["updated_at"]; ok {
		sets = append(sets, "updated_at=now()")
	}
	if len(sets) == 0 {
		return Get(ctx, db, table, id)
	}
	return returning(ctx, db, "UPDATE "+Quote(table)+" SET "+strings.Join(sets, ",")+" WHERE id=$1 RETURNING *", args...)
}
func returning(ctx context.Context, db DB, sql string, args ...any) (Record, error) {
	var b []byte
	e := db.QueryRow(ctx, "WITH changed AS ("+sql+") SELECT row_to_json(changed) FROM changed", args...).Scan(&b)
	if e != nil {
		return nil, e
	}
	var r Record
	e = json.Unmarshal(b, &r)
	return r, e
}

// Migrate applies the exact SQL of the historical Alembic revisions. Both revision
// tables are kept in sync, allowing an existing installation to adopt the Go binary.
func Migrate(ctx context.Context, pool *pgxpool.Pool) error {
	tx, e := pool.Begin(ctx)
	if e != nil {
		return e
	}
	defer tx.Rollback(ctx)
	if _, e = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(726941830)"); e != nil {
		return e
	}
	if _, e = tx.Exec(ctx, "CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32) PRIMARY KEY)"); e != nil {
		return e
	}
	var revisions []string
	rows, e := tx.Query(ctx, "SELECT version_num FROM alembic_version")
	if e != nil {
		return e
	}
	for rows.Next() {
		var s string
		if e = rows.Scan(&s); e != nil {
			rows.Close()
			return e
		}
		revisions = append(revisions, s)
	}
	rows.Close()
	if e = rows.Err(); e != nil {
		return e
	}
	if len(revisions) > 1 {
		return errors.New("multiple database revisions")
	}
	current := 0
	if len(revisions) == 1 {
		current, e = strconv.Atoi(revisions[0])
		if e != nil || current < 1 || current > LatestRevision {
			return errors.New("unsupported database revision")
		}
	}
	for n := current + 1; n <= LatestRevision; n++ {
		revision := fmt.Sprintf("%04d", n)
		sql, e := assets.ReadFile("migrations/" + revision + ".sql")
		if e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, string(sql)); e != nil {
			return fmt.Errorf("migration %s: %w", revision, e)
		}
		if _, e = tx.Exec(ctx, "DELETE FROM alembic_version"); e != nil {
			return e
		}
		if _, e = tx.Exec(ctx, "INSERT INTO alembic_version VALUES ($1)", revision); e != nil {
			return e
		}
	}
	if _, e = tx.Exec(ctx, "INSERT INTO database_schema_version (id,version,revision) VALUES (1,$1,$2) ON CONFLICT(id) DO UPDATE SET version=$1,revision=$2,updated_at=now()", LatestRevision, fmt.Sprintf("%04d", LatestRevision)); e != nil {
		return e
	}
	return tx.Commit(ctx)
}
