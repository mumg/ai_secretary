package gatewaylink

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"regexp"
	"strings"
)

var uidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

func validID(id string) bool { return uidPattern.MatchString(id) }
func keyPEM(key *ecdsa.PrivateKey) []byte {
	b, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		panic(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: b})
}
func newCSR() ([]byte, []byte, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, err
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{}}, key)
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der}), keyPEM(key), err
}
func identity(c *x509.Certificate) (string, string, error) {
	if c == nil || len(c.URIs) != 1 {
		return "", "", errors.New("invalid certificate identity")
	}
	u := c.URIs[0]
	p := strings.Split(strings.TrimPrefix(u.Path, "/"), "/")
	if u.Scheme != "spiffe" || u.Host != "ai-secretary-gateway" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.RawPath != "" || len(p) != 4 || p[0] != "installations" || p[2] != "roles" || !validID(p[1]) || (p[3] != "server" && p[3] != "client") {
		return "", "", errors.New("invalid certificate identity")
	}
	return p[1], p[3], nil
}
