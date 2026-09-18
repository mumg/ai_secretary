package server

import (
	"context"
	"encoding/json"
	"math"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestQueueStatusRateAndWebSocketSnapshots(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	states := []string{"COMPLETED", "COMPLETED", "COMPLETED", "IGNORED", "PENDING", "PROCESSING", "FAILED"}
	var pendingID any
	_, err := s.job(context.Background(), func(q *request) bool {
		for i, state := range states {
			event := threadTestMail(t, state+time.Duration(i).String(), "").Event
			event["source_id"] = "mail"
			event["source_type"] = "imap"
			event["direction"] = "INCOMING"
			event["content_hash"] = time.Duration(i).String()
			event["analysis_state"] = state
			age := 5 * time.Minute
			if i == 2 {
				age = 20 * time.Minute
			}
			if state == "COMPLETED" || state == "IGNORED" {
				event["analyzed_at"] = time.Now().Add(-age)
			}
			row := q.insert("communication_events", event)
			if state == "PENDING" {
				pendingID = row["id"]
			}
		}
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
	s.live.ready = true
	server := httptest.NewServer(s)
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	connect := func() *websocket.Conn {
		conn, _, err := websocket.Dial(ctx, "ws"+strings.TrimPrefix(server.URL, "http")+"/api/v1/realtime?status=1", nil)
		if err != nil {
			t.Fatal(err)
		}
		return conn
	}
	read := func(conn *websocket.Conn) M {
		for {
			_, data, err := conn.Read(ctx)
			if err != nil {
				t.Fatal(err)
			}
			var frame M
			if err := json.Unmarshal(data, &frame); err != nil {
				t.Fatal(err)
			}
			if frame["type"] == "ping" {
				if err := conn.Write(ctx, websocket.MessageText, []byte("pong")); err != nil {
					t.Fatal(err)
				}
			}
			if frame["type"] == "status" {
				for _, v := range obj(frame, "data")["components"].([]any) {
					row := M(v.(map[string]any))
					if row["id"] == "processing" {
						return obj(row, "metrics")
					}
				}
				t.Fatal("processing component missing")
			}
		}
	}
	conn := connect()
	defer conn.CloseNow()
	metrics := read(conn)
	if num(metrics, "events_total") != 7 || num(metrics, "events_completed") != 3 || num(metrics, "events_excluded") != 1 || num(metrics, "events_processing") != 1 || num(metrics, "events_completed_last_15m") != 2 || math.Abs(num(metrics, "events_rate_per_minute")-2.0/15) > 1e-9 {
		t.Fatal(metrics)
	}
	_, err = s.Pool.Exec(ctx, "UPDATE communication_events SET analysis_state='COMPLETED',analyzed_at=now() WHERE id=$1", pendingID)
	if err != nil {
		t.Fatal(err)
	}
	s.publish("events")
	for num(metrics, "events_completed") != 4 {
		metrics = read(conn)
	}
	if num(metrics, "events_pending") != 0 || num(metrics, "events_completed_last_15m") != 3 {
		t.Fatal(metrics)
	}
	conn.CloseNow()
	reconnected := connect()
	defer reconnected.CloseNow()
	if num(read(reconnected), "events_completed") != 4 {
		t.Fatal("reconnect did not receive current snapshot")
	}
}
