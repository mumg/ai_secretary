package store

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
)

func TestUpgradeExistingRevisionPreservesData(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set TEST_DATABASE_URL")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	name := "go_test_" + strings.ReplaceAll(UUID(), "-", "")
	if _, err = admin.Exec(ctx, "CREATE SCHEMA "+Quote(name)); err != nil {
		t.Fatal(err)
	}
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = name + ",public"
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { pool.Close(); admin.Exec(ctx, "DROP SCHEMA "+Quote(name)+" CASCADE"); admin.Close() })
	for revision := 1; revision <= 21; revision++ {
		sql, err := assets.ReadFile(fmt.Sprintf("migrations/%04d.sql", revision))
		if err != nil {
			t.Fatal(err)
		}
		if _, err = pool.Exec(ctx, string(sql)); err != nil {
			t.Fatal(revision, err)
		}
	}
	if _, err = pool.Exec(ctx, "CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY); INSERT INTO alembic_version VALUES ('0021')"); err != nil {
		t.Fatal(err)
	}
	task, err := Insert(ctx, pool, "tasks", Record{"title": "Существующая задача", "manually_created": true})
	if err != nil {
		t.Fatal(err)
	}
	if err = Migrate(ctx, pool); err != nil {
		t.Fatal(err)
	}
	if err = Migrate(ctx, pool); err != nil {
		t.Fatal("repeat", err)
	}
	persisted, err := Get(ctx, pool, "tasks", task["id"])
	if err != nil || persisted["title"] != task["title"] || persisted["created_at"] != task["created_at"] {
		t.Fatal(persisted, err)
	}
	var revision string
	if err = pool.QueryRow(ctx, "SELECT version_num FROM alembic_version").Scan(&revision); err != nil || revision != "0025" {
		t.Fatal(revision, err)
	}
	pool.Exec(ctx, "UPDATE alembic_version SET version_num='9999'")
	if err = Migrate(ctx, pool); err == nil {
		t.Fatal("future database revision accepted")
	}
}
