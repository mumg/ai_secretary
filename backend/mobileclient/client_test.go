package mobileclient

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"math/big"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
)

func identityFixture(t *testing.T) ([]byte, []byte) {
	t.Helper()
	k, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	c := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Test mobile"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	der, e := x509.CreateCertificate(rand.Reader, c, c, &k.PublicKey, k)
	if e != nil {
		t.Fatal(e)
	}
	scalar, e := mobileqr.Scalar(k)
	if e != nil {
		t.Fatal(e)
	}
	return der, scalar
}
func TestCompactQRInteroperatesWithServer(t *testing.T) {
	der, k := identityFixture(t)
	qr, e := mobileqr.Encode(mobileqr.DirectPrefix, []byte("https://example.com"), der, k)
	if e != nil {
		t.Fatal(e)
	}
	id, e := Parse(qr)
	if e != nil {
		t.Fatal(e)
	}
	if id.Server != "https://example.com" || id.Client.Leaf.Subject.CommonName != "Test mobile" {
		t.Fatal("identity mismatch")
	}
	for _, bad := range []string{qr[:len(qr)-1], "AI-SECRETARY:D9:ABC", "AI-SECRETARY:D2:!", "AI-SECRETARY:D2:"} {
		if _, e := Parse(bad); e == nil {
			t.Fatal("accepted corrupt QR")
		}
	}
	_, other := identityFixture(t)
	qr, _ = mobileqr.Encode(mobileqr.DirectPrefix, []byte("https://example.com"), der, other)
	if _, e := Parse(qr); e == nil {
		t.Fatal("accepted mismatched key")
	}
}
func TestOrigins(t *testing.T) {
	for _, s := range []string{"http://host", "https://a/b", "https://user:pass@host", "https://host?x=y", "https://host#x", "https://"} {
		if _, e := Origin(s); e == nil {
			t.Fatal("accepted", s)
		}
	}
	if v, e := Origin("example.com:8443/"); e != nil || v != "https://example.com:8443" {
		t.Fatal(v, e)
	}
}
func TestDirectConnectionRequiresQR(t *testing.T) {
	if client, err := New("https://example.com", ""); err == nil || client != nil {
		t.Fatal("accepted a direct connection without a client certificate")
	}
}
func TestMTLSAndRedirectIsolation(t *testing.T) {
	der, k := identityFixture(t)
	pair, e := pair(der, k, true)
	if e != nil {
		t.Fatal(e)
	}
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if len(r.TLS.PeerCertificates) != 1 || !r.TLS.PeerCertificates[0].Equal(pair.Leaf) {
			t.Error("client identity missing")
		}
		if r.URL.Path == "/api/v1/redirect" {
			http.Redirect(w, r, "https://example.com", 302)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"overall_status":"ok","components":[]}`))
	}))
	server.TLS = &tls.Config{ClientAuth: tls.RequireAnyClientCert}
	server.StartTLS()
	defer server.Close()
	qr, _ := mobileqr.Encode(mobileqr.DirectPrefix, []byte(server.URL), der, k)
	client, e := New("", qr)
	if e != nil {
		t.Fatal(e)
	}
	defer client.Close()
	pool := x509.NewCertPool()
	pool.AddCert(server.Certificate())
	client.transport.TLSClientConfig.RootCAs = pool
	data, e := client.Request(context.Background(), "GET", "/api/v1/system/status", nil)
	if e != nil || !json.Valid(data) {
		t.Fatal(string(data), e)
	}
	if _, e = client.Request(context.Background(), "GET", "/api/v1/redirect", nil); e == nil {
		t.Fatal("followed redirect")
	}
	for _, path := range []string{"https://example.com/api/v1/tasks", "//evil/api/v1/tasks", "/admin"} {
		if _, e = client.Request(context.Background(), "GET", path, nil); e == nil {
			t.Fatal("accepted cross-origin path")
		}
	}
	client.Close()
	if _, e = client.Request(context.Background(), "GET", "/api/v1/tasks", nil); e == nil {
		t.Fatal("closed client still active")
	}
}
