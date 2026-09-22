package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/gatewaylink"
	"golang.org/x/oauth2/jwt"
)

type pushNotice struct {
	ID, Kind, ObjectID string
	Expires            time.Time
}
type notificationTransport interface {
	Targets(context.Context) ([]string, error)
	Send(context.Context, string, pushNotice) error
}
type serverPushTransport struct{ server *Server }

func (t *serverPushTransport) gateway() (*gatewaylink.NotificationClient, bool, error) {
	b, err := os.ReadFile(filepath.Join(t.server.Config.DataDir, "gateway.json"))
	if os.IsNotExist(err) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	var cfg gatewaySettings
	if err = json.Unmarshal(b, &cfg); err != nil {
		return nil, false, err
	}
	if !cfg.Enabled {
		return nil, false, nil
	}
	client, err := gatewaylink.NewNotificationClient(t.server.gatewaySettingsDir(cfg))
	return client, true, err
}
func (t *serverPushTransport) Targets(ctx context.Context) ([]string, error) {
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	client, enabled, err := t.gateway()
	if err != nil {
		return nil, err
	}
	if enabled {
		defer client.Close()
		ids, err := client.Devices(ctx)
		for i := range ids {
			ids[i] = "gateway:" + ids[i]
		}
		return ids, err
	}
	if t.server.Config.LocalOnly {
		return nil, nil
	}
	q := &request{Context: ctx, db: t.server.Pool, server: t.server}
	if len(q.rows("SELECT id FROM system_settings WHERE id=1 AND firebase_credentials_encrypted IS NOT NULL AND firebase_credentials_encrypted<>''")) == 0 {
		return nil, nil
	}
	ids := []string{}
	for _, d := range q.rows("SELECT id FROM devices WHERE active") {
		ids = append(ids, "direct:"+str(d, "id"))
	}
	return ids, nil
}
func (t *serverPushTransport) Send(ctx context.Context, endpoint string, n pushNotice) error {
	if strings.HasPrefix(endpoint, "gateway:") {
		client, enabled, err := t.gateway()
		if err != nil {
			return err
		}
		if !enabled {
			return errors.New("gateway disabled")
		}
		defer client.Close()
		return client.Send(ctx, n.ID, strings.TrimPrefix(endpoint, "gateway:"), n.Kind, n.ObjectID, n.Expires)
	}
	q := &request{Context: ctx, db: t.server.Pool, server: t.server}
	devices := q.rows("SELECT * FROM devices WHERE id=$1 AND active", strings.TrimPrefix(endpoint, "direct:"))
	if len(devices) == 0 {
		return errors.New("device unavailable")
	}
	device := devices[0]
	rows := q.rows("SELECT firebase_credentials_encrypted FROM system_settings WHERE id=1")
	if len(rows) == 0 {
		return errors.New("firebase unavailable")
	}
	plain, err := q.server.Config.Decrypt(str(rows[0], "firebase_credentials_encrypted"))
	if err != nil {
		return err
	}
	var account M
	if err = json.Unmarshal([]byte(plain), &account); err != nil {
		return err
	}
	cfg := jwt.Config{Email: str(account, "client_email"), PrivateKey: []byte(str(account, "private_key")), PrivateKeyID: str(account, "private_key_id"), Scopes: []string{"https://www.googleapis.com/auth/firebase.messaging"}, TokenURL: str(account, "token_uri")}
	client := cfg.Client(ctx)
	client.Timeout = 20 * time.Second
	data := M{"type": n.Kind, "object_id": n.ObjectID, "notification_id": n.ID}
	if strings.Contains(n.Kind, "TASK") {
		tasks := q.rows("SELECT title,description FROM tasks WHERE id=$1", n.ObjectID)
		if len(tasks) > 0 {
			data["task_title"] = bounded(str(tasks[0], "title"), 180)
			data["task_description"] = bounded(clean(str(tasks[0], "description")), 360)
		}
	}
	message := mobilePushMessage(device["fcm_token"], data, str(device, "language"))
	payload := message["message"].(M)
	ttl := max(0, int(time.Until(n.Expires).Seconds()))
	payload["android"].(M)["ttl"] = strconv.Itoa(ttl) + "s"
	payload["android"].(M)["collapse_key"] = "secretary-updates"
	headers := payload["apns"].(M)["headers"].(M)
	headers["apns-expiration"] = strconv.FormatInt(n.Expires.Unix(), 10)
	headers["apns-collapse-id"] = "secretary-updates"
	body, err := json.Marshal(message)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", "https://fcm.googleapis.com/v1/projects/"+str(account, "project_id")+"/messages:send", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 {
		return errors.New("push provider rejected notification")
	}
	return nil
}
