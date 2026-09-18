// Package mobileqr encodes small, self-contained connection bootstraps.
package mobileqr

import (
	"bytes"
	"compress/zlib"
	"crypto/ecdsa"
	"crypto/elliptic"
	"encoding/binary"
	"errors"

	"github.com/skip2/go-qrcode"
)

const DirectPrefix = "AI-SECRETARY:D2:"
const GatewayPrefix = "AI-SECRETARY:G2:"
const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"

// Encode uses length-prefixed binary fields, then DEFLATE and Base45. All output
// characters belong to QR's alphanumeric mode (11 bits per pair, not 16).
func Encode(prefix string, fields ...[]byte) (string, error) {
	var raw bytes.Buffer
	for _, field := range fields {
		if len(field) == 0 || len(field) > 4096 {
			return "", errors.New("invalid QR field size")
		}
		_ = binary.Write(&raw, binary.BigEndian, uint16(len(field)))
		raw.Write(field)
	}
	if raw.Len() > 16384 {
		return "", errors.New("QR payload too large")
	}
	var compressed bytes.Buffer
	z, err := zlib.NewWriterLevel(&compressed, zlib.BestCompression)
	if err != nil {
		return "", err
	}
	if _, err = z.Write(raw.Bytes()); err != nil {
		return "", err
	}
	if err = z.Close(); err != nil {
		return "", err
	}
	return prefix + base45(compressed.Bytes()), nil
}
func base45(data []byte) string {
	out := make([]byte, 0, (len(data)*3+1)/2)
	for i := 0; i < len(data); i += 2 {
		n := int(data[i])
		pair := i+1 < len(data)
		if pair {
			n = n*256 + int(data[i+1])
		}
		out = append(out, alphabet[n%45], alphabet[n/45%45])
		if pair {
			out = append(out, alphabet[n/(45*45)])
		}
	}
	return string(out)
}

// Scalar removes the curve identifier and duplicated public key from PKCS#8.
// The reader takes the P-256 parameters from the accompanying certificate and
// verifies that the private key matches it before accepting the identity.
func Scalar(key *ecdsa.PrivateKey) ([]byte, error) {
	if key.Curve != elliptic.P256() {
		return nil, errors.New("QR requires a P-256 key")
	}
	return key.D.FillBytes(make([]byte, 32)), nil
}
func PNG(payload string) ([]byte, error) {
	// Integer scaling avoids blurred or uneven module widths in the source image.
	return qrcode.Encode(payload, qrcode.Medium, -6)
}
