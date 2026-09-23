package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"math/big"
	"testing"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
)

func TestBridgeCancellationAndClientOwnership(t *testing.T) {
	key, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if e != nil {
		t.Fatal(e)
	}
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Test mobile"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	cert, e := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if e != nil {
		t.Fatal(e)
	}
	scalar, e := mobileqr.Scalar(key)
	if e != nil {
		t.Fatal(e)
	}
	qr, e := mobileqr.Encode(mobileqr.DirectPrefix, []byte("https://example.com"), cert, scalar)
	if e != nil {
		t.Fatal(e)
	}
	request, _ := json.Marshal(map[string]string{"op": "open", "qr": qr})
	open, e := call(request)
	if e != nil {
		t.Fatal(e)
	}
	id := open.(map[string]any)["handle"].(int64)
	invoke := func(op string, extra map[string]any) (any, error) {
		if extra == nil {
			extra = map[string]any{}
		}
		extra["op"] = op
		extra["handle"] = id
		b, _ := json.Marshal(extra)
		return call(b)
	}
	defer invoke("close", nil)
	prepared, e := invoke("prepare", nil)
	if e != nil {
		t.Fatal(e)
	}
	_, e = invoke("cancel", map[string]any{"request_id": prepared})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = invoke("request", map[string]any{"request_id": prepared, "path": "/api/v1/tasks", "method": "GET"}); e == nil {
		t.Fatal("cancelled request succeeded")
	}
	if _, exists := requests.Load(prepared); exists {
		t.Fatal("request leaked")
	}
}
