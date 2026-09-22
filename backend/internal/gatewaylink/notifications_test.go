package gatewaylink

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"
	"time"
)

func TestNotificationClientRetriesStableOpaqueRequest(t *testing.T) {
	var requests []map[string]any
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/devices" {
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"items":[{"device_id":"active","active":true},{"device_id":"inactive","active":false}]}`))
			return
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		requests = append(requests, body)
		w.WriteHeader(202)
	}))
	defer upstream.Close()
	client := &NotificationClient{client: upstream.Client(), base: upstream.URL}
	defer client.Close()
	ids, err := client.Devices(context.Background())
	if err != nil || !reflect.DeepEqual(ids, []string{"active"}) {
		t.Fatal(ids, err)
	}
	expiry := time.Now().Add(5 * time.Minute).UTC().Truncate(time.Second)
	for i := 0; i < 2; i++ {
		if err = client.Send(context.Background(), "stable-id", "active", "SYNC", "opaque-object", expiry); err != nil {
			t.Fatal(err)
		}
	}
	if len(requests) != 2 || !reflect.DeepEqual(requests[0], requests[1]) || len(requests[0]) != 5 || requests[0]["expires_at"] != expiry.Format(time.RFC3339) {
		t.Fatal(requests)
	}
}
