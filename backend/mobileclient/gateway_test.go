package mobileclient

import (
	"bufio"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/hashicorp/yamux"
	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
)

type oneConnListener struct {
	conn      net.Conn
	done      chan struct{}
	once      sync.Once
	delivered bool
}

func (l *oneConnListener) Accept() (net.Conn, error) {
	if !l.delivered {
		l.delivered = true
		return l.conn, nil
	}
	<-l.done
	return nil, net.ErrClosed
}
func (l *oneConnListener) Close() error {
	l.once.Do(func() { close(l.done); l.conn.Close() })
	return nil
}
func (l *oneConnListener) Addr() net.Addr { return l.conn.LocalAddr() }
func gatewayCert(t *testing.T, server bool, uri string) (tls.Certificate, []byte) {
	t.Helper()
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	template := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "Test only"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	if server {
		template.DNSNames = []string{"secretary.internal"}
		template.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
	}
	if uri != "" {
		u, _ := url.Parse(uri)
		template.URIs = []*url.URL{u}
	}
	der, e := x509.CreateCertificate(rand.Reader, template, template, &k.PublicKey, k)
	if e != nil {
		t.Fatal(e)
	}
	scalar, _ := mobileqr.Scalar(k)
	leaf, _ := x509.ParseCertificate(der)
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: k, Leaf: leaf}, scalar
}
func TestGatewaySharesTunnelAndVerifiesInnerPin(t *testing.T) {
	outer, key := gatewayCert(t, false, "spiffe://ai-secretary-gateway/installations/11111111-2222-3333-4444-555555555555/roles/client")
	inner, innerKey := gatewayCert(t, false, "")
	serverCert, _ := gatewayCert(t, true, "")
	pin := sha256.Sum256(serverCert.Certificate[0])
	var tunnels atomic.Int32
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	gateway := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/tunnels/client" {
			http.NotFound(w, r)
			return
		}
		if len(r.TLS.PeerCertificates) != 1 || !r.TLS.PeerCertificates[0].Equal(outer.Leaf) {
			t.Error("wrong outer client")
		}
		ws, e := websocket.Accept(w, r, &websocket.AcceptOptions{Subprotocols: []string{"ai-secretary-tunnel.v1"}})
		if e != nil {
			return
		}
		defer ws.CloseNow()
		tunnels.Add(1)
		conn := websocket.NetConn(ctx, ws, websocket.MessageBinary)
		preface := make([]byte, len("AI-SECRETARY-MUX/1\n"))
		if _, e = io.ReadFull(conn, preface); e != nil || string(preface) != "AI-SECRETARY-MUX/1\n" {
			return
		}
		cfg := yamux.DefaultConfig()
		cfg.LogOutput = io.Discard
		mux, e := yamux.Server(conn, cfg)
		if e != nil {
			return
		}
		defer mux.Close()
		for {
			stream, e := mux.AcceptStream()
			if e != nil {
				return
			}
			go func() {
				tlsConn := tls.Server(stream, &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{serverCert}, ClientAuth: tls.RequireAnyClientCert})
				if e := tlsConn.Handshake(); e != nil {
					stream.Close()
					return
				}
				if len(tlsConn.ConnectionState().PeerCertificates) != 1 || !tlsConn.ConnectionState().PeerCertificates[0].Equal(inner.Leaf) {
					t.Error("wrong inner identity")
				}
				// A tiny HTTP/1.1 server keeps stream ownership explicit in the fixture.
				reader := bufio.NewReader(tlsConn)
				defer tlsConn.Close()
				for {
					req, e := http.ReadRequest(reader)
					if e != nil {
						return
					}
					req.Body.Close()
					body := `{"ok":true}`
					_, e = io.WriteString(tlsConn, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n\r\n"+body)
					if e != nil {
						return
					}
				}
			}()
		}
	}))
	gateway.TLS = &tls.Config{ClientAuth: tls.RequireAnyClientCert}
	gateway.StartTLS()
	defer gateway.Close()
	makeClient := func(pin []byte) *Client {
		qr, _ := mobileqr.Encode(mobileqr.GatewayPrefix, []byte(gateway.URL), outer.Certificate[0], key, pin, inner.Certificate[0], innerKey)
		c, e := New("", qr)
		if e != nil {
			t.Fatal(e)
		}
		pool := x509.NewCertPool()
		pool.AddCert(gateway.Certificate())
		c.outer.Transport.(*http.Transport).TLSClientConfig.RootCAs = pool
		return c
	}
	c := makeClient(pin[:])
	defer c.Close()
	var wg sync.WaitGroup
	for range 4 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			b, e := c.Request(ctx, "GET", "/api/v1/tasks", nil)
			if e != nil || string(b) != `{"ok":true}` {
				t.Errorf("tunnel request: %s %v", b, e)
			}
		}()
	}
	wg.Wait()
	if tunnels.Load() != 1 {
		t.Fatalf("opened %d outer tunnels", tunnels.Load())
	}
	wrong := pin
	wrong[0] ^= 1
	bad := makeClient(wrong[:])
	defer bad.Close()
	if _, e := bad.Request(ctx, "GET", "/api/v1/tasks", nil); e == nil {
		t.Fatal("accepted wrong inner pin")
	}
}
