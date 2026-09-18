package server

import (
	"bytes"
	"crypto"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"math/big"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/backend/internal/mobileqr"
)

// The QR carries the identity directly: enrolling a phone does not require a
// public, unauthenticated exception to the reverse proxy's mTLS policy.
type mobileIdentity struct {
	Version     int    `json:"v"`
	Server      string `json:"server"`
	Certificate string `json:"cert"`
	Key         string `json:"key"`
}

func readClientIssuer(certFile, keyFile string) (*x509.Certificate, crypto.Signer, error) {
	invalid := errors.New("invalid client certificate issuer")
	certPEM, err := os.ReadFile(certFile)
	if err != nil {
		return nil, nil, err
	}
	block, _ := pem.Decode(certPEM)
	if block == nil {
		return nil, nil, invalid
	}
	ca, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !ca.IsCA || (ca.KeyUsage != 0 && ca.KeyUsage&x509.KeyUsageCertSign == 0) {
		return nil, nil, invalid
	}
	keyPEM, err := os.ReadFile(keyFile)
	if err != nil {
		return nil, nil, err
	}
	block, _ = pem.Decode(keyPEM)
	if block == nil {
		return nil, nil, invalid
	}
	var key any
	switch block.Type {
	case "PRIVATE KEY":
		key, err = x509.ParsePKCS8PrivateKey(block.Bytes)
	case "RSA PRIVATE KEY":
		key, err = x509.ParsePKCS1PrivateKey(block.Bytes)
	case "EC PRIVATE KEY":
		key, err = x509.ParseECPrivateKey(block.Bytes)
	default:
		return nil, nil, invalid
	}
	signer, ok := key.(crypto.Signer)
	if err != nil || !ok {
		return nil, nil, invalid
	}
	public, err := x509.MarshalPKIXPublicKey(signer.Public())
	if err != nil || !bytes.Equal(public, ca.RawSubjectPublicKeyInfo) {
		return nil, nil, invalid
	}
	now := time.Now()
	if now.Before(ca.NotBefore) || !now.Before(ca.NotAfter) {
		return nil, nil, invalid
	}
	return ca, signer, nil
}

func issueMobileIdentity(ca *x509.Certificate, signer crypto.Signer, server, label string, now time.Time) (string, time.Time, error) {
	u, err := url.Parse(server)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil ||
		(u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return "", time.Time{}, errors.New("mobile access requires an HTTPS server origin")
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", time.Time{}, err
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return "", time.Time{}, err
	}
	until := now.AddDate(1, 0, 0)
	if until.After(ca.NotAfter) {
		until = ca.NotAfter
	}
	template := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: "AI Secretary: " + label},
		NotBefore: now.Add(-5 * time.Minute), NotAfter: until, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, template, ca, &key.PublicKey, signer)
	if err != nil {
		return "", time.Time{}, err
	}
	scalar, err := mobileqr.Scalar(key)
	if err != nil {
		return "", time.Time{}, err
	}
	payload, err := mobileqr.Encode(mobileqr.DirectPrefix, []byte(strings.TrimRight(server, "/")), der, scalar)
	return payload, until, err
}

func (s *Server) mobileIdentityRoutes() {
	s.route("POST /api/v1/admin/mobile-identity", false, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		q.w.Header().Set("Pragma", "no-cache")
		if s.Config.LocalOnly {
			fail(409, "Мобильное подключение недоступно в локальном WEB-режиме")
		}
		m := q.body()
		// Accept labels from older clients, but the connection page needs no name.
		textField(m, "label", 1, 64, false)
		label := "Mobile"
		if _, supplied := m["label"]; supplied {
			label = clean(str(m, "label"))
			if label == "" {
				fail(422, "Название устройства не должно быть пустым")
			}
		}
		ca, signer, err := readClientIssuer(s.Config.ClientCAFile, s.Config.ClientCAKeyFile)
		if err != nil {
			fail(503, "Выпуск мобильных ключей не настроен: проверьте клиентский CA и его ключ на сервере")
		}
		server := str(obj(q.settings(), "server"), "public_url")
		payload, until, err := issueMobileIdentity(ca, signer, server, label, time.Now())
		if err != nil {
			fail(422, "Для мобильного приложения нужен публичный HTTPS-адрес сервера без пути и параметров")
		}
		png, err := mobileqr.PNG(payload)
		if err != nil {
			fail(422, "Сертификат слишком велик для QR-кода")
		}
		q.status = 201
		return M{"qr_image": "data:image/png;base64," + base64.StdEncoding.EncodeToString(png), "server_url": server, "expires_at": until}
	})
}
