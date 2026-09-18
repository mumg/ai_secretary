package setup

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"time"

	"software.sslmate.com/src/go-pkcs12"
)

var certificateFiles = []string{"client-ca.pem", "client-ca.key", "client.p12", "client-password.txt"}

func CreateCertificates(data string) error {
	dir := filepath.Join(data, "certificates")
	count := 0
	for _, name := range certificateFiles {
		if exists(filepath.Join(dir, name)) {
			count++
		}
	}
	if count > 0 {
		if count != len(certificateFiles) {
			return errors.New("неполный комплект сертификатов; восстановите его из резервной копии")
		}
		return validateCertificates(dir)
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return err
	}
	serial := func() (*big.Int, error) { return rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128)) }
	caSerial, err := serial()
	if err != nil {
		return err
	}
	clientSerial, err := serial()
	if err != nil {
		return err
	}
	now := time.Now()
	caTemplate := &x509.Certificate{SerialNumber: caSerial, Subject: pkix.Name{CommonName: "AI Secretary Client CA"}, NotBefore: now.Add(-5 * time.Minute), NotAfter: now.Add(3650 * 24 * time.Hour), BasicConstraintsValid: true, IsCA: true, MaxPathLen: 0, MaxPathLenZero: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		return err
	}
	ca, err := x509.ParseCertificate(caDER)
	if err != nil {
		return err
	}
	template := &x509.Certificate{SerialNumber: clientSerial, Subject: pkix.Name{CommonName: "AI Secretary client"}, NotBefore: now.Add(-5 * time.Minute), NotAfter: now.Add(730 * 24 * time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, ca, &key.PublicKey, caKey)
	if err != nil {
		return err
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		return err
	}
	password, err := secret(24)
	if err != nil {
		return err
	}
	// Pin the AES/PBKDF2 encoder; do not silently change PFX compatibility when
	// the library's default encoder changes in a future release.
	pfx, err := pkcs12.Modern2023.Encode(key, cert, []*x509.Certificate{ca}, password)
	if err != nil {
		return err
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(caKey)
	if err != nil {
		return err
	}
	blobs := [][]byte{pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: caDER}), pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER}), pfx, []byte(password)}
	// If interrupted, the partial set is rejected on the next run, never rotated.
	for i, name := range certificateFiles {
		if err = writeAtomic(filepath.Join(dir, name), blobs[i]); err != nil {
			return err
		}
	}
	return nil
}
func validateCertificates(dir string) error {
	fail := errors.New("некорректный комплект сертификатов; восстановите его из резервной копии")
	caPEM, err := os.ReadFile(filepath.Join(dir, "client-ca.pem"))
	if err != nil {
		return err
	}
	block, _ := pem.Decode(caPEM)
	if block == nil {
		return fail
	}
	ca, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !ca.IsCA {
		return fail
	}
	keyPEM, err := os.ReadFile(filepath.Join(dir, "client-ca.key"))
	if err != nil {
		return err
	}
	block, _ = pem.Decode(keyPEM)
	if block == nil {
		return fail
	}
	rawKey, err := x509.ParsePKCS8PrivateKey(block.Bytes)
	if err != nil {
		return fail
	}
	key, ok := rawKey.(*ecdsa.PrivateKey)
	if !ok {
		return fail
	}
	public, err := x509.MarshalPKIXPublicKey(&key.PublicKey)
	if err != nil || !bytes.Equal(public, ca.RawSubjectPublicKeyInfo) {
		return fail
	}
	password, err := readText(filepath.Join(dir, "client-password.txt"))
	if err != nil {
		return err
	}
	blob, err := os.ReadFile(filepath.Join(dir, "client.p12"))
	if err != nil {
		return err
	}
	clientKey, cert, chain, err := pkcs12.DecodeChain(blob, password)
	if err != nil || cert == nil || clientKey == nil || len(chain) != 1 || !bytes.Equal(chain[0].Raw, ca.Raw) || cert.CheckSignatureFrom(ca) != nil {
		return fail
	}
	clientEC, ok := clientKey.(*ecdsa.PrivateKey)
	if !ok {
		return fail
	}
	public, err = x509.MarshalPKIXPublicKey(&clientEC.PublicKey)
	if err != nil || !bytes.Equal(public, cert.RawSubjectPublicKeyInfo) {
		return fail
	}
	for _, usage := range cert.ExtKeyUsage {
		if usage == x509.ExtKeyUsageClientAuth {
			return nil
		}
	}
	return fail
}
