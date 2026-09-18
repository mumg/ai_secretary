package gatewaylink

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"path/filepath"
	"time"
)

// Notify submits only opaque identifiers. Business data stays inside inner TLS.
func Notify(ctx context.Context, dir, kind, objectID string, newID func() string) (int, error) {
	cfg, err := Load(dir)
	if err != nil {
		return 0, err
	}
	pair, err := tls.LoadX509KeyPair(filepath.Join(dir, "server.crt"), filepath.Join(dir, "server.key"))
	if err != nil {
		return 0, err
	}
	client, err := trustedHTTP("", &pair)
	if err != nil {
		return 0, err
	}
	defer client.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, "GET", cfg.Gateway+"/api/v1/devices", nil)
	if err != nil {
		return 0, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, err
	}
	var devices struct {
		Items []struct {
			ID     string `json:"device_id"`
			Active bool   `json:"active"`
		} `json:"items"`
	}
	err = json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&devices)
	resp.Body.Close()
	if err != nil || resp.StatusCode != 200 {
		return 0, errors.New("gateway device list unavailable")
	}
	ids := []string{}
	for _, d := range devices.Items {
		if d.Active {
			ids = append(ids, d.ID)
		}
	}
	accepted := 0
	for len(ids) > 0 {
		n := min(len(ids), 100)
		body, _ := json.Marshal(map[string]any{"notification_id": newID(), "device_ids": ids[:n], "type": kind, "object_id": objectID, "expires_at": time.Now().UTC().Add(time.Hour).Format(time.RFC3339)})
		req, err = http.NewRequestWithContext(ctx, "POST", cfg.Gateway+"/api/v1/notifications", bytes.NewReader(body))
		if err != nil {
			return accepted, err
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err = client.Do(req)
		if err != nil {
			return accepted, err
		}
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
		resp.Body.Close()
		if resp.StatusCode >= 300 {
			return accepted, errors.New("gateway notification rejected")
		}
		accepted += n
		ids = ids[n:]
	}
	return accepted, nil
}
