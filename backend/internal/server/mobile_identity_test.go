package server

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"math/big"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/config"
	"github.com/skip2/go-qrcode"
)

func TestMobileIdentityQRAndMTLS(t *testing.T) {
	// Exercise the largest deployed issuer (Docker's RSA-4096 CA).
	caKey, err := rsa.GenerateKey(rand.Reader, 4096)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "TEST ONLY Client CA"},
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign, NotBefore: now.Add(-time.Hour), NotAfter: now.AddDate(2, 0, 0)}
	caDER, err := x509.CreateCertificate(rand.Reader, template, template, &caKey.PublicKey, caKey)
	if err != nil {
		t.Fatal(err)
	}
	ca, _ := x509.ParseCertificate(caDER)
	dir := t.TempDir()
	certFile, keyFile := filepath.Join(dir, "ca.crt"), filepath.Join(dir, "ca.key")
	os.WriteFile(certFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), 0600)
	os.WriteFile(keyFile, pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(caKey)}), 0600)
	if _, _, err := readClientIssuer(certFile, keyFile); err != nil {
		t.Fatal(err)
	}
	payload, until, err := issueMobileIdentity(ca, caKey, "https://secretary.example.test/", "Тестовый телефон", now)
	if err != nil {
		t.Fatal(err)
	}
	if !until.Equal(now.AddDate(1, 0, 0)) {
		t.Fatal("unexpected certificate lifetime")
	}
	png, err := qrcode.Encode(payload, qrcode.Medium, 768)
	if err != nil {
		t.Fatal("identity does not fit QR:", err)
	}
	var identity mobileIdentity
	if err := json.Unmarshal([]byte(strings.TrimPrefix(payload, "ai-secretary:identity:")), &identity); err != nil {
		t.Fatal(err)
	}
	if identity.Version != 1 || identity.Server != "https://secretary.example.test" {
		t.Fatal("invalid QR contract")
	}
	der, _ := base64.StdEncoding.DecodeString(identity.Certificate)
	keyDER, _ := base64.StdEncoding.DecodeString(identity.Key)
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	key, err := x509.ParsePKCS8PrivateKey(keyDER)
	if err != nil {
		t.Fatal(err)
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if _, err := cert.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}); err != nil {
		t.Fatal(err)
	}
	if _, err := cert.Verify(x509.VerifyOptions{Roots: roots, KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err == nil {
		t.Fatal("client identity must not serve as server identity")
	}
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if len(r.TLS.VerifiedChains) == 0 {
			t.Error("missing verified client certificate")
		}
		w.WriteHeader(204)
	}))
	server.TLS = &tls.Config{ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: roots, MinVersion: tls.VersionTLS12}
	server.StartTLS()
	defer server.Close()
	client := server.Client()
	transport := client.Transport.(*http.Transport).Clone()
	transport.TLSClientConfig.Certificates = []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}
	client.Transport = transport
	response, err := client.Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != 204 {
		t.Fatal(response.Status)
	}
	// Optional generated interoperability fixtures, used only in androidTest assets.
	if fixtureDir := os.Getenv("MOBILE_IDENTITY_TEST_FIXTURE_DIR"); fixtureDir != "" {
		if err := os.MkdirAll(fixtureDir, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(fixtureDir, "test-identity.txt"), []byte(payload), 0600); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(fixtureDir, "test-identity.png"), png, 0600); err != nil {
			t.Fatal(err)
		}
	}
	// Every generation must issue a different private key/certificate.
	second, _, err := issueMobileIdentity(ca, caKey, identity.Server, "Phone", now)
	if err != nil || second == payload {
		t.Fatal("identity was reused")
	}
	for _, origin := range []string{"http://example.test", "https://x.test/path", "https://u:p@x.test", "https://x.test?q=secret"} {
		if _, _, err := issueMobileIdentity(ca, caKey, origin, "Phone", now); err == nil {
			t.Fatal("accepted invalid origin", origin)
		}
	}
	wrongKey, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	wrongDER, _ := x509.MarshalPKCS8PrivateKey(wrongKey)
	os.WriteFile(keyFile, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: wrongDER}), 0600)
	if _, _, err := readClientIssuer(certFile, keyFile); err == nil {
		t.Fatal("accepted mismatched issuer")
	}
}

func TestMobileIdentityLocalOnlyAndCSRF(t *testing.T) {
	s := New(nil, config.Config{LocalOnly: true}, "test")
	for _, origin := range []string{"", "https://attacker.test"} {
		request := httptest.NewRequest("POST", "http://localhost/api/v1/admin/mobile-identity", strings.NewReader(`{"label":"Phone"}`))
		request.Header.Set("Origin", origin)
		response := httptest.NewRecorder()
		s.ServeHTTP(response, request)
		expected := 409
		if origin != "" {
			expected = 403
		}
		if response.Code != expected {
			t.Fatalf("got %d, want %d", response.Code, expected)
		}
	}
}

func TestMobileIdentityEndpoint(t *testing.T) {
	s := testServer(t)
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	// Some existing OpenSSL issuers omit KeyUsage entirely; this remains valid.
	ca := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "TEST ONLY"}, IsCA: true, BasicConstraintsValid: true, NotBefore: now.Add(-time.Hour), NotAfter: now.AddDate(2, 0, 0)}
	der, err := x509.CreateCertificate(rand.Reader, ca, ca, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	s.Config.ClientCAFile = filepath.Join(dir, "ca.crt")
	s.Config.ClientCAKeyFile = filepath.Join(dir, "ca.key")
	os.WriteFile(s.Config.ClientCAFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0600)
	os.WriteFile(s.Config.ClientCAKeyFile, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER}), 0600)
	result := call(t, s, "POST", "/api/v1/admin/mobile-identity", M{"label": "My phone"}, 201).(map[string]any)
	if !strings.HasPrefix(result["qr_image"].(string), "data:image/png;base64,") || result["server_url"] != "https://localhost" {
		t.Fatal("invalid QR response")
	}
	call(t, s, "POST", "/api/v1/admin/mobile-identity", M{"label": " "}, 422)
	call(t, s, "POST", "/api/v1/admin/mobile-identity", M{"label": strings.Repeat("x", 65)}, 422)
	call(t, s, "POST", "/api/v1/admin/mobile-identity", M{"label": "phone", "secret": "forbidden"}, 422)
	req := httptest.NewRequest("POST", "/api/v1/admin/mobile-identity", strings.NewReader(`{"label":"Phone"}`))
	response := httptest.NewRecorder()
	s.ServeHTTP(response, req)
	if response.Code != 201 || response.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("QR must not be cached")
	}
	os.Remove(s.Config.ClientCAKeyFile)
	call(t, s, "POST", "/api/v1/admin/mobile-identity", M{"label": "phone"}, 503)
}
