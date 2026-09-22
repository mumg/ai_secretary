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

// NotificationClient reuses mTLS for a policy batch. IDs and expiry must remain
// stable across retries, so gateway acceptance is idempotent per device.
type NotificationClient struct {
	client *http.Client
	base   string
}

func NewNotificationClient(dir string) (*NotificationClient, error) {
	cfg, err := Load(dir)
	if err != nil {
		return nil, err
	}
	pair, err := tls.LoadX509KeyPair(filepath.Join(dir, "server.crt"), filepath.Join(dir, "server.key"))
	if err != nil {
		return nil, err
	}
	client, err := trustedHTTP("", &pair)
	if err != nil {
		return nil, err
	}
	return &NotificationClient{client: client, base: cfg.Gateway}, nil
}
func (c *NotificationClient) Close() { c.client.CloseIdleConnections() }
func (c *NotificationClient) Devices(ctx context.Context) ([]string, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", c.base+"/api/v1/devices", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, errors.New("gateway device list unavailable")
	}
	var body struct {
		Items []struct {
			ID     string `json:"device_id"`
			Active bool   `json:"active"`
		} `json:"items"`
	}
	if err = json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&body); err != nil {
		return nil, err
	}
	ids := []string{}
	for _, d := range body.Items {
		if d.Active {
			ids = append(ids, d.ID)
		}
	}
	return ids, nil
}
func (c *NotificationClient) Send(ctx context.Context, notificationID, deviceID, kind, objectID string, expires time.Time) error {
	body, _ := json.Marshal(map[string]any{"notification_id": notificationID, "device_ids": []string{deviceID}, "type": kind, "object_id": objectID, "expires_at": expires.UTC().Format(time.RFC3339)})
	req, err := http.NewRequestWithContext(ctx, "POST", c.base+"/api/v1/notifications", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		return errors.New("gateway notification rejected")
	}
	return nil
}
