package mobileclient

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand/v2"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/coder/websocket"
	"github.com/hashicorp/yamux"
)

type Client struct {
	Identity  *Identity
	HTTP      *http.Client
	outer     *http.Client
	transport *http.Transport
	ctx       context.Context
	cancel    context.CancelFunc
	mu        sync.Mutex
	mux       *yamux.Session
	events    chan string
	watch     sync.Once
}

func New(server, qr string) (*Client, error) {
	if qr == "" {
		return nil, errors.New("QR-код не содержит клиентский сертификат")
	}
	id, err := Parse(qr)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithCancel(context.Background())
	c := &Client{Identity: id, ctx: ctx, cancel: cancel, events: make(chan string, 1)}
	tlsConfig := &tls.Config{MinVersion: tls.VersionTLS13}
	tlsConfig.Certificates = []tls.Certificate{id.Client}
	outer := &http.Transport{TLSClientConfig: tlsConfig, TLSHandshakeTimeout: 15 * time.Second, ResponseHeaderTimeout: 30 * time.Second, IdleConnTimeout: 30 * time.Second}
	c.outer = &http.Client{Transport: outer, CheckRedirect: noRedirect}
	c.transport = outer
	if id.Installation != "" {
		c.transport = &http.Transport{DialTLSContext: c.dialInner, ResponseHeaderTimeout: 30 * time.Second, IdleConnTimeout: 30 * time.Second}
	}
	c.HTTP = &http.Client{Transport: c.transport, Timeout: 45 * time.Second, CheckRedirect: noRedirect}
	return c, nil
}
func noRedirect(_ *http.Request, _ []*http.Request) error {
	return errors.New("Перенаправление подключения запрещено")
}
func (c *Client) dialInner(ctx context.Context, network, addr string) (net.Conn, error) {
	u, _ := url.Parse(c.Identity.Server)
	expected := u.Host
	if u.Port() == "" {
		expected = net.JoinHostPort(u.Hostname(), "443")
	}
	if addr != expected {
		return nil, errors.New("Неожиданный адрес туннеля")
	}
	c.mu.Lock()
	if c.ctx.Err() != nil {
		c.mu.Unlock()
		return nil, net.ErrClosed
	}
	if c.mux == nil || c.mux.IsClosed() {
		// The handshake is bounded by the caller. Established connection lives for
		// the client lifetime, not the lifetime of the first HTTP request.
		ws, _, err := websocket.Dial(ctx, "wss"+strings.TrimPrefix(c.Identity.Server, "https")+"/api/v1/tunnels/client", &websocket.DialOptions{HTTPClient: c.outer, Subprotocols: []string{"ai-secretary-tunnel.v1"}})
		if err != nil {
			c.mu.Unlock()
			return nil, err
		}
		if ws.Subprotocol() != "ai-secretary-tunnel.v1" {
			ws.CloseNow()
			c.mu.Unlock()
			return nil, errors.New("Неверный протокол шлюза")
		}
		conn := websocket.NetConn(c.ctx, ws, websocket.MessageBinary)
		ws.SetReadLimit(1 << 20)
		conn.SetWriteDeadline(time.Now().Add(15 * time.Second))
		if _, err = io.WriteString(conn, "AI-SECRETARY-MUX/1\n"); err != nil {
			conn.Close()
			c.mu.Unlock()
			return nil, err
		}
		conn.SetWriteDeadline(time.Time{})
		config := yamux.DefaultConfig()
		config.LogOutput = io.Discard
		config.StreamOpenTimeout = 20 * time.Second
		c.mux, err = yamux.Client(conn, config)
		if err != nil {
			conn.Close()
			c.mu.Unlock()
			return nil, err
		}
	}
	mux := c.mux
	c.mu.Unlock()
	stream, err := mux.OpenStream()
	if err != nil {
		return nil, err
	}
	config := &tls.Config{MinVersion: tls.VersionTLS13, ServerName: "secretary.internal", Certificates: []tls.Certificate{c.Identity.Inner},
		// The QR carries the exact self-signed inner server fingerprint. Public
		// system roots are used for the outer WSS; only this inner layer is pinned.
		InsecureSkipVerify: true, VerifyConnection: func(s tls.ConnectionState) error {
			if len(s.PeerCertificates) != 1 {
				return errors.New("Неверная цепочка внутреннего сервера")
			}
			cert := s.PeerCertificates[0]
			pin := sha256.Sum256(cert.Raw)
			if subtle.ConstantTimeCompare(pin[:], c.Identity.Pin) != 1 || time.Now().Before(cert.NotBefore) || !time.Now().Before(cert.NotAfter) {
				return errors.New("Сертификат внутреннего сервера не совпадает")
			}
			return cert.VerifyHostname("secretary.internal")
		}}
	conn := tls.Client(stream, config)
	if err = conn.HandshakeContext(ctx); err != nil {
		conn.Close()
		return nil, err
	}
	return conn, nil
}
func (c *Client) Request(ctx context.Context, method, path string, body []byte) (json.RawMessage, error) {
	u, err := url.Parse(path)
	if err != nil || u.IsAbs() || u.Host != "" || !strings.HasPrefix(u.Path, "/api/v1/") || u.Fragment != "" {
		return nil, errors.New("Недопустимый путь API")
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	stop := context.AfterFunc(c.ctx, cancel)
	defer stop()
	req, err := http.NewRequestWithContext(ctx, method, c.Identity.Server+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	if len(body) > 0 {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return nil, errors.New("Нет соединения с сервером. Проверьте сеть и сертификат")
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, (8<<20)+1))
	if err != nil {
		return nil, err
	}
	if len(b) > 8<<20 {
		return nil, errors.New("Ответ сервера слишком велик")
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("Сервер вернул HTTP %d", resp.StatusCode)
	}
	if len(b) == 0 {
		return json.RawMessage("null"), nil
	}
	if !json.Valid(b) {
		return nil, errors.New("Сервер вернул некорректный JSON")
	}
	return b, nil
}
func (c *Client) StartRealtime() { c.watch.Do(func() { go c.realtime() }) }
func (c *Client) NextEvent(ctx context.Context) string {
	select {
	case e := <-c.events:
		return e
	case <-ctx.Done():
		return "timeout"
	case <-c.ctx.Done():
		return "closed"
	}
}
func (c *Client) notify(s string) {
	select {
	case c.events <- s:
	default:
	}
}
func (c *Client) realtime() {
	delay := time.Second
	for c.ctx.Err() == nil {
		ctx, cancel := context.WithTimeout(c.ctx, 25*time.Second)
		ws, _, err := websocket.Dial(ctx, "wss"+strings.TrimPrefix(c.Identity.Server, "https")+"/api/v1/realtime", &websocket.DialOptions{HTTPClient: &http.Client{Transport: c.transport, CheckRedirect: noRedirect}})
		cancel()
		if err == nil {
			delay = time.Second
			ws.SetReadLimit(65536)
			c.notify("changed")
			for {
				readCtx, stop := context.WithTimeout(c.ctx, 60*time.Second)
				_, b, e := ws.Read(readCtx)
				stop()
				if e != nil {
					break
				}
				var msg struct {
					Type string `json:"type"`
				}
				if json.Unmarshal(b, &msg) != nil {
					break
				}
				if msg.Type == "ping" {
					writeCtx, stop := context.WithTimeout(c.ctx, 10*time.Second)
					e = ws.Write(writeCtx, websocket.MessageText, []byte("pong"))
					stop()
					if e != nil {
						break
					}
				} else if msg.Type == "changed" {
					c.notify("changed")
				}
			}
			ws.CloseNow()
		}
		c.notify("offline")
		timer := time.NewTimer(delay + time.Duration(rand.IntN(500))*time.Millisecond)
		select {
		case <-c.ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
		delay = min(30*time.Second, delay*2)
	}
}
func (c *Client) Close() {
	c.cancel()
	c.mu.Lock()
	if c.mux != nil {
		c.mux.Close()
	}
	c.mu.Unlock()
	c.transport.CloseIdleConnections()
	c.outer.CloseIdleConnections()
}

// RegisterPush uses the gateway's outer API, never the server's inner API.
// The caller persists the random device key/revision before sending a new token.
func (c *Client) RegisterPush(ctx context.Context, deviceID, key string, revision int64, token string) error {
	if c.Identity.Installation == "" || len(deviceID) != 36 || len(key) < 32 || len(token) < 10 || len(token) > 10000 || revision < 1 {
		return errors.New("Некорректная регистрация push")
	}
	b, _ := json.Marshal(map[string]any{"fcm_token": token, "revision": revision})
	ctx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()
	stop := context.AfterFunc(c.ctx, cancel)
	defer stop()
	req, err := http.NewRequestWithContext(ctx, "PUT", c.Identity.Server+"/api/v1/devices/"+deviceID, bytes.NewReader(b))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Device-Key", key)
	resp, err := c.outer.Do(req)
	if err != nil {
		return errors.New("Не удалось зарегистрировать push на шлюзе")
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("Регистрация push: HTTP %d", resp.StatusCode)
	}
	return nil
}
