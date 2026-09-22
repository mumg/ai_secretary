package server

import (
	"context"
	"log/slog"
	"sync"
	"time"
)

// Commit heartbeats independently: the import transaction can run for minutes.
// Stop and join before writing the final status so BUSY cannot overwrite it.
func (s *Server) sourceHeartbeat(ctx context.Context, id string, interval time.Duration) func() {
	ctx, cancel := context.WithCancel(ctx)
	pulse := func() {
		if ctx.Err() != nil {
			return
		}
		// Drain a started transaction instead of cancelling COMMIT: pgx may
		// return on cancellation before PostgreSQL finishes applying the write.
		// stop() joins this bounded operation before the final source status.
		pulseCtx, done := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
		defer done()
		_, err := s.job(pulseCtx, func(q *request) bool {
			// Serialize with deletion without blocking foreign keys held by the import.
			q.exec("SELECT id FROM communication_sources WHERE id=$1 FOR NO KEY UPDATE", id)
			rows := q.rows("SELECT * FROM communication_sources WHERE id=$1 AND enabled", id)
			if len(rows) == 0 {
				cancel()
				return false
			}
			q.component("source-"+id, M{"label": rows[0]["label"], "component_type": "event_loader", "status": "BUSY", "message": "Загрузка данных из источника", "metrics": M{}, "ttl_seconds": 90})
			return true
		})
		if err != nil && ctx.Err() == nil {
			slog.Warn("source heartbeat failed", "source_id", id)
		}
	}
	pulse()
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				pulse()
			}
		}
	}()
	var once sync.Once
	return func() { once.Do(func() { cancel(); <-finished }) }
}
