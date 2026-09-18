// Transport adapted from ai-secretary-gateway/internal/connector; protocol v1.
package gatewaylink

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/hashicorp/yamux"
	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
)

type Config struct {
	Gateway      string `json:"gateway"`
	Installation string `json:"installation_id"`
}
type QR struct {
	Version     int    `json:"v"`
	Gateway     string `json:"gateway"`
	ID          string `json:"installation_id"`
	ClientCert  string `json:"cert"`
	ClientKey   string `json:"key"`
	InnerServer string `json:"inner_server"`
	InnerClient string `json:"inner_client"`
	InnerKey    string `json:"inner_key"`
}

func WriteSecret(path string, v []byte) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = f.Write(v)
	return err
}
func trustedHTTP(rootFile string, cert *tls.Certificate) (*http.Client, error) {
	pool, err := x509.SystemCertPool()
	if err != nil {
		pool = x509.NewCertPool()
	}
	if rootFile != "" {
		b, e := os.ReadFile(rootFile)
		if e != nil {
			return nil, e
		}
		if !pool.AppendCertsFromPEM(b) {
			return nil, errors.New("invalid gateway trust PEM")
		}
	}
	c := &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: pool}
	if cert != nil {
		c.Certificates = []tls.Certificate{*cert}
	}
	return &http.Client{Transport: &http.Transport{TLSClientConfig: c}, Timeout: 30 * time.Second, CheckRedirect: func(r *http.Request, via []*http.Request) error { return http.ErrUseLastResponse }}, nil
}
func Enroll(ctx context.Context, dir, base, id, rootFile string) error {
	u, err := url.Parse(base)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" || !validID(id) {
		return errors.New("invalid gateway origin or UID")
	}
	base = strings.TrimRight(base, "/")
	if err = os.Mkdir(dir, 0700); err != nil {
		return errors.New("enrollment directory must not already exist")
	}
	sc, sk, err := newCSR()
	if err != nil {
		return err
	}
	cc, ck, err := newCSR()
	if err != nil {
		return err
	}
	for n, b := range map[string][]byte{"server.key": sk, "client.key": ck, "server.csr": sc, "client.csr": cc} {
		if err = WriteSecret(filepath.Join(dir, n), b); err != nil {
			return err
		}
	}
	h, err := trustedHTTP(rootFile, nil)
	if err != nil {
		return err
	}
	body, _ := json.Marshal(map[string]string{"installation_id": id, "server_csr_pem": string(sc), "client_csr_pem": string(cc)})
	req, err := http.NewRequestWithContext(ctx, "POST", base+"/api/v1/installations", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := h.Do(req)
	if err != nil {
		return errors.New("registration response unavailable; do not reissue for the same UID")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 201 {
		return errors.New("registration rejected: " + resp.Status)
	}
	var out struct {
		ID     string `json:"installation_id"`
		Server string `json:"server_certificate_pem"`
		Client string `json:"client_certificate_pem"`
		CA     string `json:"ca_certificate_pem"`
	}
	if err = json.NewDecoder(io.LimitReader(resp.Body, 32768)).Decode(&out); err != nil {
		return err
	}
	if out.ID != id {
		return errors.New("registration UID mismatch")
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM([]byte(out.CA)) {
		return errors.New("invalid issuer")
	}
	for role, v := range map[string]struct {
		cert string
		key  []byte
	}{"server": {out.Server, sk}, "client": {out.Client, ck}} {
		pair, e := tls.X509KeyPair([]byte(v.cert), v.key)
		if e != nil {
			return e
		}
		cert, e := x509.ParseCertificate(pair.Certificate[0])
		if e != nil {
			return e
		}
		if _, e = cert.Verify(x509.VerifyOptions{Roots: pool, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); e != nil {
			return e
		}
		cid, cr, e := identity(cert)
		if e != nil || cid != id || cr != role {
			return errors.New("certificate identity mismatch")
		}
		if e = WriteSecret(filepath.Join(dir, role+".crt"), []byte(v.cert)); e != nil {
			return e
		}
	}
	if err = WriteSecret(filepath.Join(dir, "issuer.crt"), []byte(out.CA)); err != nil {
		return err
	}
	cfg, _ := json.MarshalIndent(Config{base, id}, "", "  ")
	if err = WriteSecret(filepath.Join(dir, "config.json"), cfg); err != nil {
		return err
	}
	if err = innerFiles(dir); err != nil {
		return err
	}
	return WriteQR(dir)
}
func leaf(name string, usage x509.ExtKeyUsage) ([]byte, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	sn, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, nil, err
	}
	t := &x509.Certificate{SerialNumber: sn, Subject: pkix.Name{CommonName: name}, DNSNames: []string{name}, NotBefore: time.Now().Add(-time.Minute), NotAfter: time.Now().AddDate(1, 0, 0), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{usage}, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, t, t, &key.PublicKey, key)
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), keyPEM(key), err
}
func innerFiles(dir string) error {
	for role, usage := range map[string]x509.ExtKeyUsage{"inner-server": x509.ExtKeyUsageServerAuth, "inner-client": x509.ExtKeyUsageClientAuth} {
		name := "secretary.internal"
		if role == "inner-client" {
			name = "mobile.internal"
		}
		c, k, err := leaf(name, usage)
		if err != nil {
			return err
		}
		if err = WriteSecret(filepath.Join(dir, role+".crt"), c); err != nil {
			return err
		}
		if err = WriteSecret(filepath.Join(dir, role+".key"), k); err != nil {
			return err
		}
	}
	return nil
}
func Load(dir string) (Config, error) {
	var c Config
	b, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err == nil {
		err = json.Unmarshal(b, &c)
	}
	return c, err
}
func der(dir, name string) (string, error) {
	b, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		return "", err
	}
	p, _ := pem.Decode(b)
	if p == nil {
		return "", errors.New("invalid PEM")
	}
	return base64.RawURLEncoding.EncodeToString(p.Bytes), nil
}

// QRPayload reuses existing credentials; displaying the compact QR never rotates
// a registration, keys, or certificates.
func QRPayload(dir string) (string, error) {
	cfg, err := Load(dir)
	if err != nil {
		return "", err
	}
	readDER := func(name string) ([]byte, error) {
		value, e := der(dir, name)
		if e != nil {
			return nil, e
		}
		return base64.RawURLEncoding.DecodeString(value)
	}
	fields := [][]byte{[]byte(cfg.Gateway)}
	for _, role := range []string{"client", "inner-client"} {
		cert, e := readDER(role + ".crt")
		if e != nil {
			return "", e
		}
		raw, e := readDER(role + ".key")
		if e != nil {
			return "", e
		}
		key, e := x509.ParsePKCS8PrivateKey(raw)
		if e != nil {
			return "", e
		}
		ec, ok := key.(*ecdsa.PrivateKey)
		if !ok {
			return "", errors.New("QR requires an EC key")
		}
		scalar, e := mobileqr.Scalar(ec)
		if e != nil {
			return "", e
		}
		fields = append(fields, cert, scalar)
		if role == "client" {
			server, e := readDER("inner-server.crt")
			if e != nil {
				return "", e
			}
			pin := sha256.Sum256(server)
			fields = append(fields, pin[:])
		}
	}
	return mobileqr.Encode(mobileqr.GatewayPrefix, fields...)
}
func QRImage(dir string) ([]byte, error) {
	payload, err := QRPayload(dir)
	if err != nil {
		return nil, err
	}
	return mobileqr.PNG(payload)
}
func WriteQR(dir string) error {
	payload, err := QRPayload(dir)
	if err != nil {
		return err
	}
	png, err := mobileqr.PNG(payload)
	if err != nil {
		return err
	}
	if err = WriteSecret(filepath.Join(dir, "mobile-qr.txt"), []byte(payload)); err != nil {
		return err
	}
	return WriteSecret(filepath.Join(dir, "mobile-qr.png"), png)
}
func Run(ctx context.Context, dir string, handler http.Handler, status func(bool)) error {
	cfg, err := Load(dir)
	if err != nil {
		return err
	}
	ext, err := tls.LoadX509KeyPair(filepath.Join(dir, "server.crt"), filepath.Join(dir, "server.key"))
	if err != nil {
		return err
	}
	h, err := trustedHTTP("", &ext)
	if err != nil {
		return err
	}
	h.Timeout = 0
	inner, err := tls.LoadX509KeyPair(filepath.Join(dir, "inner-server.crt"), filepath.Join(dir, "inner-server.key"))
	if err != nil {
		return err
	}
	roots := x509.NewCertPool()
	b, err := os.ReadFile(filepath.Join(dir, "inner-client.crt"))
	if err != nil {
		return err
	}
	if !roots.AppendCertsFromPEM(b) {
		return errors.New("invalid inner client identity")
	}
	tc := &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{inner}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots}
	backoff := time.Second
	for {
		if ctx.Err() != nil {
			return nil
		}
		start := time.Now()
		_ = session(ctx, cfg, h, tc, handler, status)
		status(false)
		if ctx.Err() != nil {
			return nil
		}
		if time.Since(start) > time.Minute {
			backoff = time.Second
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 30*time.Second)
	}
}
func session(parent context.Context, cfg Config, h *http.Client, tc *tls.Config, handler http.Handler, status func(bool)) error {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	ws, _, err := websocket.Dial(ctx, "wss"+strings.TrimPrefix(cfg.Gateway, "https")+"/api/v1/tunnels/server", &websocket.DialOptions{HTTPClient: h, Subprotocols: []string{"ai-secretary-tunnel.v1"}})
	if err != nil {
		return err
	}
	defer ws.CloseNow()
	conn := websocket.NetConn(ctx, ws, websocket.MessageBinary)
	ws.SetReadLimit(1 << 20)
	s, err := yamux.Client(conn, yamuxConfig())
	if err != nil {
		return err
	}
	defer s.Close()
	control, err := s.OpenStream()
	if err != nil {
		return err
	}
	defer control.Close()
	status(true)
	// Keep the authenticated HTTP control stream alive. Business notifications may use
	// the public mTLS HTTP API as well; both enforce the same role and UID checks.
	transport := &http.Transport{DialContext: func(context.Context, string, string) (net.Conn, error) { return control, nil }, MaxConnsPerHost: 1}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 10 * time.Second}
	go func() {
		t := time.NewTicker(20 * time.Second)
		defer t.Stop()
		for {
			req, _ := http.NewRequestWithContext(ctx, "GET", "http://gateway/api/v1/devices", nil)
			resp, e := client.Do(req)
			if e != nil {
				cancel()
				return
			}
			_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
			_ = resp.Body.Close()
			if resp.StatusCode != 200 {
				cancel()
				return
			}
			select {
			case <-ctx.Done():
				return
			case <-t.C:
			}
		}
	}()
	listener := newMobileListener(s)
	srv := &http.Server{Handler: handler, ReadHeaderTimeout: 15 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10}
	go func() { <-ctx.Done(); _ = srv.Close(); _ = s.Close() }()
	return srv.Serve(tls.NewListener(listener, tc))
}

// net/http briefly interrupts reads while hijacking a connection. yamux marks
// its timeout non-temporary, which crypto/tls otherwise remembers permanently.
// Preserve normal net.Conn timeout semantics so inner WebSocket upgrades work.
type tlsStream struct{ net.Conn }

func (s *tlsStream) Read(p []byte) (int, error) {
	n, err := s.Conn.Read(p)
	if errors.Is(err, yamux.ErrTimeout) {
		err = os.ErrDeadlineExceeded
	}
	return n, err
}
func (s *tlsStream) Write(p []byte) (int, error) {
	n, err := s.Conn.Write(p)
	if errors.Is(err, yamux.ErrTimeout) {
		err = os.ErrDeadlineExceeded
	}
	return n, err
}

func yamuxConfig() *yamux.Config {
	c := yamux.DefaultConfig()
	c.KeepAliveInterval = 20 * time.Second
	c.ConnectionWriteTimeout = 15 * time.Second
	c.StreamOpenTimeout = 15 * time.Second
	c.StreamCloseTimeout = 15 * time.Second
	c.MaxStreamWindowSize = 256 << 10
	c.AcceptBacklog = 32
	c.LogOutput = io.Discard
	return c
}
