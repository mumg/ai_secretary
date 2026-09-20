// An isolated, synthetic HTTPS/mTLS API for simulator integration checks.
package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"flag"
	"fmt"
	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"time"
)

func must[T any](v T, e error) T {
	if e != nil {
		panic(e)
	}
	return v
}
func main() {
	out := flag.String("output", "", "private fixture directory")
	flag.Parse()
	if *out == "" {
		panic("output required")
	}
	must(0, os.MkdirAll(*out, 0700))
	caKey := must(ecdsa.GenerateKey(elliptic.P256(), rand.Reader))
	now := time.Now()
	ca := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "AI Secretary simulator test CA"}, NotBefore: now.Add(-time.Minute), NotAfter: now.Add(2 * time.Hour), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	caDER := must(x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey))
	ca = must(x509.ParseCertificate(caDER))
	leaf := func(server bool) (tls.Certificate, []byte) {
		k := must(ecdsa.GenerateKey(elliptic.P256(), rand.Reader))
		t := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "Simulator fixture"}, NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
		if server {
			t.SerialNumber = big.NewInt(3)
			t.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
			t.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
		}
		der := must(x509.CreateCertificate(rand.Reader, t, ca, &k.PublicKey, caKey))
		return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: k}, must(mobileqr.Scalar(k))
	}
	client, key := leaf(false)
	server, _ := leaf(true)
	pool := x509.NewCertPool()
	pool.AddCert(ca)
	var mu sync.Mutex
	tasks := []map[string]any{}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		defer mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		encode := func(v any) { json.NewEncoder(w).Encode(v) }
		switch r.URL.Path {
		case "/api/v1/system/status":
			encode(map[string]any{"overall_status": "OK", "components": []any{}})
		case "/api/v1/tasks":
			if r.Method == "POST" {
				var task map[string]any
				if json.NewDecoder(r.Body).Decode(&task) != nil || task["priority"] != "NORMAL" {
					w.WriteHeader(422)
					encode(map[string]string{"detail": "priority must be NORMAL"})
					return
				}
				task["id"] = "11111111-2222-3333-4444-555555555555"
				task["status"] = "NEW"
				tasks = append(tasks, task)
				w.WriteHeader(201)
				encode(task)
			} else {
				encode(tasks)
			}
		case "/api/v1/plans/today":
			items := []any{}
			for _, t := range tasks {
				items = append(items, map[string]any{"task": t})
			}
			encode(map[string]any{"items": items, "meetings": []any{}})
		default:
			w.WriteHeader(404)
			encode(map[string]string{"detail": "fixture route not found"})
		}
	})
	service := httptest.NewUnstartedServer(handler)
	service.TLS = &tls.Config{MinVersion: tls.VersionTLS13, Certificates: []tls.Certificate{server}, ClientAuth: tls.RequireAndVerifyClientCert, ClientCAs: pool}
	service.StartTLS()
	defer service.Close()
	qr := must(mobileqr.Encode(mobileqr.DirectPrefix, []byte(service.URL), client.Certificate[0], key))
	must(0, os.WriteFile(filepath.Join(*out, "ca.crt"), pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), 0600))
	data := must(json.Marshal(map[string]string{"server": service.URL, "qr": qr}))
	must(0, os.WriteFile(filepath.Join(*out, "connection.json"), data, 0600))
	fmt.Println("Simulator TLS fixture ready")
	select {}
}
