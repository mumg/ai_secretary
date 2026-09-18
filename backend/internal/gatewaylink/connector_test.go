package gatewaylink

import (
	"bufio"
	"bytes"
	"compress/zlib"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/hashicorp/yamux"
	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
	"github.com/mumg/ai_secretary/backend/internal/mobileqr/testutil"
	"github.com/skip2/go-qrcode"
)

// A protocol peer with an ephemeral CA; no real gateway/account credentials.
type testGateway struct {
	server  *httptest.Server
	ca      *x509.Certificate
	key     *ecdsa.PrivateKey
	caPEM   []byte
	mu      sync.Mutex
	session *yamux.Session
	issued  map[string]bool
	clients int
	opened  int
	sockets map[*websocket.Conn]bool
}

func newTestGateway(t *testing.T, android bool) *testGateway {
	t.Helper()
	key, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "TEST ONLY gateway CA"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(10, 0, 0), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	raw, e := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if e != nil {
		t.Fatal(e)
	}
	ca, _ := x509.ParseCertificate(raw)
	g := &testGateway{ca: ca, key: key, caPEM: pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: raw}), issued: make(map[string]bool), sockets: make(map[*websocket.Conn]bool)}
	g.server = httptest.NewUnstartedServer(http.HandlerFunc(g.serve))
	if android {
		listener, e := net.Listen("tcp", "127.0.0.1:18443")
		if e != nil {
			t.Fatal(e)
		}
		g.server.Listener.Close()
		g.server.Listener = listener
	}
	sk, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	st := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "localhost"}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, DNSNames: []string{"localhost"}, NotBefore: template.NotBefore, NotAfter: template.NotAfter, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, KeyUsage: x509.KeyUsageDigitalSignature}
	sr, _ := x509.CreateCertificate(rand.Reader, st, ca, &sk.PublicKey, key)
	pair, _ := tls.X509KeyPair(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: sr}), keyPEM(sk))
	pool := x509.NewCertPool()
	pool.AddCert(ca)
	g.server.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{pair}, ClientAuth: tls.VerifyClientCertIfGiven, ClientCAs: pool}
	g.server.StartTLS()
	t.Cleanup(g.server.Close)
	return g
}
func (g *testGateway) serve(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/api/v1/installations" {
		if r.Header.Get("Authorization") != "" {
			http.Error(w, "registration must not send an invitation", 400)
			return
		}
		g.mu.Lock()
		defer g.mu.Unlock()
		var body map[string]string
		if json.NewDecoder(r.Body).Decode(&body) != nil {
			http.Error(w, "json", 400)
			return
		}
		if g.issued[body["installation_id"]] {
			http.Error(w, "used", 409)
			return
		}
		g.issued[body["installation_id"]] = true
		if _, exists := body["name"]; exists {
			http.Error(w, "name is unnecessary", 400)
			return
		}
		out := map[string]string{"installation_id": body["installation_id"], "ca_certificate_pem": string(g.caPEM)}
		for i, role := range []string{"server", "client"} {
			block, _ := pem.Decode([]byte(body[role+"_csr_pem"]))
			csr, e := x509.ParseCertificateRequest(block.Bytes)
			if e != nil || csr.CheckSignature() != nil {
				http.Error(w, "csr", 400)
				return
			}
			uri, _ := url.Parse("spiffe://ai-secretary-gateway/installations/" + body["installation_id"] + "/roles/" + role)
			cert := &x509.Certificate{SerialNumber: big.NewInt(int64(i + 10)), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(5, 0, 0), URIs: []*url.URL{uri}, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}, KeyUsage: x509.KeyUsageDigitalSignature, BasicConstraintsValid: true}
			der, _ := x509.CreateCertificate(rand.Reader, cert, g.ca, csr.PublicKey, g.key)
			out[role+"_certificate_pem"] = string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}))
		}
		w.WriteHeader(201)
		json.NewEncoder(w).Encode(out)
		return
	}
	if len(r.TLS.VerifiedChains) == 0 {
		http.Error(w, "mTLS required", 401)
		return
	}
	_, role, e := identity(r.TLS.PeerCertificates[0])
	if e != nil {
		http.Error(w, "identity", 403)
		return
	}
	if r.URL.Path == "/test/drop-tunnels" && role == "client" {
		g.mu.Lock()
		sockets := make([]*websocket.Conn, 0, len(g.sockets))
		for ws := range g.sockets {
			sockets = append(sockets, ws)
		}
		g.mu.Unlock()
		for _, ws := range sockets {
			ws.CloseNow()
		}
		w.WriteHeader(204)
		return
	}
	if r.URL.Path == "/test/tunnel-stats" && role == "client" {
		g.mu.Lock()
		defer g.mu.Unlock()
		json.NewEncoder(w).Encode(map[string]int{"active": g.clients, "opened": g.opened})
		return
	}
	if r.URL.Path == "/api/v1/tunnels/server" && role == "server" {
		ws, e := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"ai-secretary-tunnel.v1"}})
		if e != nil {
			return
		}
		defer ws.CloseNow()
		ws.SetReadLimit(1 << 20)
		session, e := yamux.Server(websocket.NetConn(r.Context(), ws, websocket.MessageBinary), yamuxConfig())
		if e != nil {
			return
		}
		defer session.Close()
		control, e := session.AcceptStream()
		if e != nil {
			return
		}
		defer control.Close()
		g.mu.Lock()
		g.session = session
		g.mu.Unlock()
		reader := bufio.NewReader(control)
		for {
			req, e := http.ReadRequest(reader)
			if e != nil {
				return
			}
			req.Body.Close()
			io.WriteString(control, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 12\r\n\r\n{\"items\":[]}")
		}
	}
	if r.URL.Path == "/api/v1/tunnels/client" && role == "client" {
		g.mu.Lock()
		session := g.session
		// Match the production gateway's per-installation stream limit.
		if g.clients >= 32 {
			g.mu.Unlock()
			http.Error(w, "too_many_streams", 429)
			return
		}
		g.clients++
		g.opened++
		g.mu.Unlock()
		defer func() { g.mu.Lock(); g.clients--; g.mu.Unlock() }()
		if session == nil {
			http.Error(w, "offline", 503)
			return
		}
		stream, e := session.OpenStream()
		if e != nil {
			http.Error(w, "offline", 503)
			return
		}
		defer stream.Close()
		ws, e := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"ai-secretary-tunnel.v1"}})
		if e != nil {
			return
		}
		g.mu.Lock()
		g.sockets[ws] = true
		g.mu.Unlock()
		defer func() { g.mu.Lock(); delete(g.sockets, ws); g.mu.Unlock() }()
		defer ws.CloseNow()
		ws.SetReadLimit(1 << 20)
		conn := websocket.NetConn(r.Context(), ws, websocket.MessageBinary)
		done := make(chan struct{})
		go func() { io.Copy(stream, conn); stream.Close(); close(done) }()
		io.Copy(conn, stream)
		conn.Close()
		<-done
		return
	}
	http.NotFound(w, r)
}
func testEnrollment(t *testing.T, g *testGateway) string {
	t.Helper()
	parent := t.TempDir()
	root := filepath.Join(parent, "ca.crt")
	os.WriteFile(root, g.caPEM, 0600)
	dir := filepath.Join(parent, "identity")
	if e := Enroll(context.Background(), dir, g.server.URL, "11111111-1111-4111-8111-111111111111", root); e != nil {
		t.Fatal(e)
	}
	return dir
}
func startTestConnector(t *testing.T, g *testGateway, dir string) {
	t.Helper()
	cfg, e := Load(dir)
	if e != nil {
		t.Fatal(e)
	}
	ext, e := tls.LoadX509KeyPair(filepath.Join(dir, "server.crt"), filepath.Join(dir, "server.key"))
	if e != nil {
		t.Fatal(e)
	}
	client, e := trustedHTTP(filepath.Join(filepath.Dir(dir), "ca.crt"), &ext)
	if e != nil {
		t.Fatal(e)
	}
	client.Timeout = 0
	inner, e := tls.LoadX509KeyPair(filepath.Join(dir, "inner-server.crt"), filepath.Join(dir, "inner-server.key"))
	if e != nil {
		t.Fatal(e)
	}
	roots := x509.NewCertPool()
	b, _ := os.ReadFile(filepath.Join(dir, "inner-client.crt"))
	roots.AppendCertsFromPEM(b)
	tc := &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{inner}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	ready := make(chan struct{})
	var once sync.Once
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/realtime" {
			ws, e := websocket.Accept(w, r, nil)
			if e != nil {
				return
			}
			defer ws.CloseNow()
			for {
				typ, data, err := ws.Read(r.Context())
				if err != nil {
					return
				}
				if ws.Write(r.Context(), typ, data) != nil {
					return
				}
			}
		}
		if r.URL.Path == "/test/echo" {
			data, err := io.ReadAll(io.LimitReader(r.Body, 3<<20))
			if err != nil {
				return
			}
			w.Write(data)
			return
		}
		if r.URL.Path == "/test/wait" {
			<-r.Context().Done()
			return
		}
		w.Header().Set("Content-Type", "application/json")
		io.WriteString(w, `{"overall_status":"ok","components":[],"transport":"gateway"}`)
	})
	go func() {
		_ = session(ctx, cfg, client, tc, handler, func(ok bool) {
			if ok {
				once.Do(func() { close(ready) })
			}
		})
	}()
	select {
	case <-ready:
	case <-time.After(10 * time.Second):
		t.Fatal("connector timeout")
	}
}
func TestEnrollmentAndInnerTLS(t *testing.T) {
	g := newTestGateway(t, false)
	dir := testEnrollment(t, g)
	startTestConnector(t, g, dir)
	text, e := os.ReadFile(filepath.Join(dir, "mobile-qr.txt"))
	if e != nil {
		t.Fatal(e)
	}
	fields, e := testutil.Decode(string(text), mobileqr.GatewayPrefix, 6)
	if e != nil {
		t.Fatal(e)
	}
	bootstrapCert, e := x509.ParseCertificate(fields[1])
	if e != nil {
		t.Fatal(e)
	}
	id, role, e := identity(bootstrapCert)
	if string(fields[0]) != g.server.URL || id != "11111111-1111-4111-8111-111111111111" || role != "client" || e != nil {
		t.Fatal("QR mismatch")
	}
	if e = Enroll(context.Background(), dir, g.server.URL, id, filepath.Join(filepath.Dir(dir), "ca.crt")); e == nil {
		t.Fatal("overwrote enrollment")
	}
	legacy := legacyPayload(t, dir)
	oldCode, _ := qrcode.New(legacy, qrcode.Medium)
	compactCode, _ := qrcode.New(string(text), qrcode.Medium)
	if compactCode.VersionNumber >= oldCode.VersionNumber {
		t.Fatal("QR density did not decrease")
	}
	t.Logf("Gateway: %d -> %d chars; QR version %d -> %d", len(legacy), len(text), oldCode.VersionNumber, compactCode.VersionNumber)
	ext, _ := tls.LoadX509KeyPair(filepath.Join(dir, "client.crt"), filepath.Join(dir, "client.key"))
	outer, _ := trustedHTTP(filepath.Join(filepath.Dir(dir), "ca.crt"), &ext)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	ws, _, e := websocket.Dial(ctx, g.server.URL+"/api/v1/tunnels/client", &websocket.DialOptions{HTTPClient: outer, Subprotocols: []string{"ai-secretary-tunnel.v1"}})
	if e != nil {
		t.Fatal(e)
	}
	defer ws.CloseNow()
	cert, _ := tls.LoadX509KeyPair(filepath.Join(dir, "inner-client.crt"), filepath.Join(dir, "inner-client.key"))
	roots := x509.NewCertPool()
	b, _ := os.ReadFile(filepath.Join(dir, "inner-server.crt"))
	roots.AppendCertsFromPEM(b)
	conn := tls.Client(websocket.NetConn(ctx, ws, websocket.MessageBinary), &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots, ServerName: "secretary.internal", Certificates: []tls.Certificate{cert}})
	defer conn.Close()
	if e = conn.HandshakeContext(ctx); e != nil {
		t.Fatal(e)
	}
	io.WriteString(conn, "GET /api/v1/system/status HTTP/1.1\r\nHost: secretary.internal\r\nConnection: close\r\n\r\n")
	resp, e := http.ReadResponse(bufio.NewReader(conn), nil)
	if e != nil {
		t.Fatal(e)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 || !bytes.Contains(body, []byte(`"transport":"gateway"`)) {
		t.Fatal(string(body))
	}
	transport := &http.Transport{DialTLSContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
		next, _, err := websocket.Dial(ctx, g.server.URL+"/api/v1/tunnels/client", &websocket.DialOptions{HTTPClient: outer, Subprotocols: []string{"ai-secretary-tunnel.v1"}})
		if err != nil {
			return nil, err
		}
		connection := tls.Client(websocket.NetConn(context.Background(), next, websocket.MessageBinary), &tls.Config{MinVersion: tls.VersionTLS13, RootCAs: roots, ServerName: "secretary.internal", Certificates: []tls.Certificate{cert}})
		return connection, connection.HandshakeContext(ctx)
	}}
	defer transport.CloseIdleConnections()
	realtime, _, err := websocket.Dial(ctx, g.server.URL+"/api/v1/realtime", &websocket.DialOptions{HTTPClient: &http.Client{Transport: transport}})
	if err != nil {
		t.Fatal("realtime handshake", err)
	}
	defer realtime.CloseNow()
	if err = realtime.Write(ctx, websocket.MessageText, []byte("ping")); err != nil {
		t.Fatal(err)
	}
	_, message, err := realtime.Read(ctx)
	if err != nil || string(message) != "ping" {
		t.Fatal("realtime", string(message), err)
	}

}

// Optional live protocol peer for Android instrumentation. adb reverse tcp:18443 tcp:18443.
func TestAndroidGatewayPeer(t *testing.T) {
	fixture := os.Getenv("ANDROID_GATEWAY_FIXTURE_DIR")
	if fixture == "" {
		t.Skip("Android interop fixture not requested")
	}
	g := newTestGateway(t, true)
	dir := testEnrollment(t, g)
	startTestConnector(t, g, dir)
	os.MkdirAll(fixture, 0700)
	for _, name := range []string{"mobile-qr.txt", "mobile-qr.png"} {
		b, _ := os.ReadFile(filepath.Join(dir, name))
		if e := os.WriteFile(filepath.Join(fixture, name), b, 0600); e != nil {
			t.Fatal(e)
		}
	}
	legacy := legacyPayload(t, dir)
	os.WriteFile(filepath.Join(fixture, "legacy-qr.txt"), []byte(legacy), 0600)
	os.WriteFile(filepath.Join(fixture, "gateway-ca.crt"), g.caPEM, 0600)
	for i := 0; i < 600; i++ {
		if _, e := os.Stat(filepath.Join(fixture, "done")); e == nil {
			return
		}
		time.Sleep(time.Second)
	}
	t.Fatal("Android instrumentation did not finish")
}

func TestFreshUIDIssuesFreshCertificatesAndQRWithoutInvitation(t *testing.T) {
	g := newTestGateway(t, false)
	first := testEnrollment(t, g)
	second := filepath.Join(filepath.Dir(first), "replacement")
	root := filepath.Join(filepath.Dir(first), "ca.crt")
	id := "22222222-2222-4222-8222-222222222222"
	if err := Enroll(context.Background(), second, g.server.URL, id, root); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"server.key", "client.key", "server.crt", "client.crt", "inner-server.crt", "mobile-qr.png"} {
		a, err := os.ReadFile(filepath.Join(first, name))
		if err != nil {
			t.Fatal(err)
		}
		b, err := os.ReadFile(filepath.Join(second, name))
		if err != nil {
			t.Fatal(err)
		}
		if bytes.Equal(a, b) {
			t.Fatalf("replacement reused %s", name)
		}
	}
	if err := Enroll(context.Background(), filepath.Join(filepath.Dir(first), "duplicate"), g.server.URL, id, root); err == nil {
		t.Fatal("same UID was accepted twice")
	}
}

func legacyPayload(t *testing.T, dir string) string {
	t.Helper()
	cfg, e := Load(dir)
	if e != nil {
		t.Fatal(e)
	}
	q := QR{Version: 1, Gateway: cfg.Gateway, ID: cfg.Installation}
	for name, dst := range map[string]*string{"client.crt": &q.ClientCert, "client.key": &q.ClientKey, "inner-server.crt": &q.InnerServer, "inner-client.crt": &q.InnerClient, "inner-client.key": &q.InnerKey} {
		*dst, e = der(dir, name)
		if e != nil {
			t.Fatal(e)
		}
	}
	raw, _ := json.Marshal(q)
	var out bytes.Buffer
	z := zlib.NewWriter(&out)
	z.Write(raw)
	z.Close()
	return "ai-secretary:gateway:v1:" + base64.RawURLEncoding.EncodeToString(out.Bytes())
}

func TestCompactQRReusesLegacyRegistration(t *testing.T) {
	g := newTestGateway(t, false)
	dir := testEnrollment(t, g)
	before := map[string][]byte{}
	for _, name := range []string{"client.key", "client.crt", "inner-server.crt", "inner-client.key", "inner-client.crt", "config.json"} {
		value, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatal(err)
		}
		before[name] = value
	}
	legacy := legacyPayload(t, dir)
	oldPNG, err := qrcode.Encode(legacy, qrcode.Medium, -6)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(dir, "mobile-qr.png"), oldPNG, 0600); err != nil {
		t.Fatal(err)
	}
	compact, err := QRImage(dir)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Equal(compact, oldPNG) {
		t.Fatal("cached old QR returned")
	}
	again, err := QRImage(dir)
	if err != nil || !bytes.Equal(compact, again) {
		t.Fatal("QR changed existing identity", err)
	}
	for name, want := range before {
		got, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil || !bytes.Equal(got, want) {
			t.Fatalf("registration changed: %s", name)
		}
	}
}
