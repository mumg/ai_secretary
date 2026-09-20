package main

import (
	"encoding/json"
	"testing"
)

func TestBridgeCancellationAndClientOwnership(t *testing.T) {
	open, e := call([]byte(`{"op":"open","server":"https://example.com"}`))
	if e != nil {
		t.Fatal(e)
	}
	id := open.(map[string]any)["handle"].(int64)
	invoke := func(op string, extra map[string]any) (any, error) {
		if extra == nil {
			extra = map[string]any{}
		}
		extra["op"] = op
		extra["handle"] = id
		b, _ := json.Marshal(extra)
		return call(b)
	}
	defer invoke("close", nil)
	prepared, e := invoke("prepare", nil)
	if e != nil {
		t.Fatal(e)
	}
	_, e = invoke("cancel", map[string]any{"request_id": prepared})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = invoke("request", map[string]any{"request_id": prepared, "path": "/api/v1/tasks", "method": "GET"}); e == nil {
		t.Fatal("cancelled request succeeded")
	}
	if _, exists := requests.Load(prepared); exists {
		t.Fatal("request leaked")
	}
}
