package gatewaylink

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"path/filepath"
	"time"
)

type DiagnosticClient struct {
	client *http.Client
	base   string
}

type DiagnosticStatus struct {
	State    string              `json:"state"`
	Response string              `json:"response"`
	Messages []DiagnosticMessage `json:"messages,omitempty"`
}

type DiagnosticMessage struct {
	ID      int64           `json:"id"`
	Kind    string          `json:"kind"`
	Public  bool            `json:"public"`
	Content json.RawMessage `json:"content"`
}

func NewDiagnosticClient(dir string) (*DiagnosticClient, error) {
	cfg, err := Load(dir)
	if err != nil {
		return nil, err
	}
	parsed, err := url.Parse(cfg.Gateway)
	if err != nil || parsed.Scheme != "https" || parsed.Host == "" || parsed.User != nil || parsed.Path != "" || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, errors.New("invalid diagnostic gateway address")
	}
	pair, err := tls.LoadX509KeyPair(filepath.Join(dir, "server.crt"), filepath.Join(dir, "server.key"))
	if err != nil {
		return nil, err
	}
	client, err := trustedHTTP("", &pair)
	if err != nil {
		return nil, err
	}
	return &DiagnosticClient{client: client, base: cfg.Gateway}, nil
}
func (c *DiagnosticClient) Close() { c.client.CloseIdleConnections() }
func (c *DiagnosticClient) Delete(ctx context.Context, id string) error {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "DELETE", c.base+"/api/v1/diagnostic-reports/"+url.PathEscape(id), nil)
	if err != nil {
		return err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1024))
		return errors.New("gateway did not delete diagnostic report")
	}
	return nil
}
func (c *DiagnosticClient) Send(ctx context.Context, id, payload string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "POST", c.base+"/api/v1/diagnostic-reports", bytes.NewBufferString(payload))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Idempotency-Key", id)
	resp, err := c.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusAccepted {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1024))
		return "", errors.New("gateway did not accept diagnostic report")
	}
	var receipt struct {
		State string `json:"state"`
	}
	if err = json.NewDecoder(io.LimitReader(resp.Body, 2048)).Decode(&receipt); err != nil {
		return "", err
	}
	return receipt.State, nil
}
func (c *DiagnosticClient) Status(ctx context.Context, id string) (DiagnosticStatus, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", c.base+"/api/v1/diagnostic-reports/"+id, nil)
	if err != nil {
		return DiagnosticStatus{}, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return DiagnosticStatus{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return DiagnosticStatus{}, errors.New("gateway diagnostic status unavailable")
	}
	var result DiagnosticStatus
	if err = json.NewDecoder(io.LimitReader(resp.Body, 256<<10)).Decode(&result); err != nil {
		return DiagnosticStatus{}, err
	}
	return result, nil
}
func (c *DiagnosticClient) Statuses(ctx context.Context) (map[string]DiagnosticStatus, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, "GET", c.base+"/api/v1/diagnostic-reports", nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, errors.New("gateway diagnostic list unavailable")
	}
	var page struct {
		Items []struct {
			ID string `json:"report_id"`
			DiagnosticStatus
		} `json:"items"`
	}
	if err = json.NewDecoder(io.LimitReader(resp.Body, 512<<10)).Decode(&page); err != nil {
		return nil, err
	}
	out := map[string]DiagnosticStatus{}
	for _, report := range page.Items {
		out[report.ID] = report.DiagnosticStatus
	}
	return out, nil
}
