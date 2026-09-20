// Package mobileclient implements the Android-compatible mobile wire protocol.
// It has no storage: the iOS caller keeps the bootstrap only in its Keychain.
package mobileclient

import (
	"bytes"
	"compress/zlib"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net/url"
	"regexp"
	"strings"
	"time"
)

var gatewayURI = regexp.MustCompile(`^spiffe://ai-secretary-gateway/installations/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/roles/client$`)

type Identity struct {
	Server       string
	Installation string
	Client       tls.Certificate
	Inner        tls.Certificate
	Pin          []byte
}

func Origin(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if !strings.Contains(raw, "://") {
		raw = "https://" + raw
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || strings.ContainsAny(u.Host, " \\\t\r\n") {
		return "", errors.New("Укажите HTTPS-адрес без пути и параметров")
	}
	u.Path = ""
	return u.String(), nil
}
func inflate(data []byte) ([]byte, error) {
	z, err := zlib.NewReader(bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	defer z.Close()
	b, err := io.ReadAll(io.LimitReader(z, 16385))
	if err != nil || len(b) > 16384 {
		return nil, errors.New("Некорректный размер QR")
	}
	return b, nil
}
func fields(raw string, n int) ([][]byte, error) {
	const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
	var zipped []byte
	for len(raw) > 0 {
		count := 3
		if len(raw) < 3 {
			count = len(raw)
		}
		if count == 1 {
			return nil, errors.New("Некорректный Base45")
		}
		value, mult := 0, 1
		for _, ch := range raw[:count] {
			i := strings.IndexRune(alphabet, ch)
			if i < 0 {
				return nil, errors.New("Некорректный Base45")
			}
			value += i * mult
			mult *= 45
		}
		if count == 3 {
			if value > 65535 {
				return nil, errors.New("Некорректный Base45")
			}
			zipped = append(zipped, byte(value/256), byte(value))
		} else {
			if value > 255 {
				return nil, errors.New("Некорректный Base45")
			}
			zipped = append(zipped, byte(value))
		}
		raw = raw[count:]
	}
	b, err := inflate(zipped)
	if err != nil {
		return nil, err
	}
	out := make([][]byte, 0, n)
	for len(b) > 0 {
		if len(b) < 2 {
			return nil, errors.New("Обрезанный QR")
		}
		size := int(binary.BigEndian.Uint16(b))
		b = b[2:]
		if size == 0 || size > 4096 || size > len(b) {
			return nil, errors.New("Некорректное поле QR")
		}
		out = append(out, b[:size])
		b = b[size:]
	}
	if len(out) != n {
		return nil, errors.New("Неверная версия QR")
	}
	return out, nil
}
func pair(der, key []byte, compact bool) (tls.Certificate, error) {
	var result tls.Certificate
	c, err := x509.ParseCertificate(der)
	if err != nil {
		return result, err
	}
	validUsage := false
	for _, u := range c.ExtKeyUsage {
		if u == x509.ExtKeyUsageClientAuth {
			validUsage = true
		}
	}
	if c.IsCA || !validUsage || time.Now().Before(c.NotBefore) || !time.Now().Before(c.NotAfter) {
		return result, errors.New("Недействующий клиентский сертификат")
	}
	var k *ecdsa.PrivateKey
	if compact {
		if len(key) != 32 {
			return result, errors.New("Неверный размер ключа")
		}
		d := new(big.Int).SetBytes(key)
		curve := elliptic.P256()
		if d.Sign() <= 0 || d.Cmp(curve.Params().N) >= 0 {
			return result, errors.New("Некорректный ключ")
		}
		x, y := curve.ScalarBaseMult(key)
		k = &ecdsa.PrivateKey{PublicKey: ecdsa.PublicKey{Curve: curve, X: x, Y: y}, D: d}
	} else {
		parsed, e := x509.ParsePKCS8PrivateKey(key)
		if e != nil {
			return result, e
		}
		var ok bool
		k, ok = parsed.(*ecdsa.PrivateKey)
		if !ok {
			return result, errors.New("Требуется ключ EC")
		}
	}
	pub, ok := c.PublicKey.(*ecdsa.PublicKey)
	if !ok || !pub.Equal(&k.PublicKey) {
		return result, errors.New("Ключ не соответствует сертификату")
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: k, Leaf: c}, nil
}
func Parse(raw string) (*Identity, error) {
	if len(raw) > 8192 {
		return nil, errors.New("Слишком большой QR")
	}
	id := &Identity{}
	var f [][]byte
	var err error
	compact := strings.HasPrefix(raw, "AI-SECRETARY:")
	gateway := strings.HasPrefix(raw, "AI-SECRETARY:G2:") || strings.HasPrefix(raw, "ai-secretary:gateway:v1:")
	if compact {
		switch {
		case strings.HasPrefix(raw, "AI-SECRETARY:D2:"):
			f, err = fields(strings.TrimPrefix(raw, "AI-SECRETARY:D2:"), 3)
		case gateway:
			f, err = fields(strings.TrimPrefix(raw, "AI-SECRETARY:G2:"), 6)
		default:
			return nil, errors.New("Неизвестная версия QR")
		}
		if err != nil {
			return nil, err
		}
	} else {
		var m struct {
			V            int    `json:"v"`
			Server       string `json:"server"`
			Gateway      string `json:"gateway"`
			Installation string `json:"installation_id"`
			Cert         string `json:"cert"`
			Key          string `json:"key"`
			InnerServer  string `json:"inner_server"`
			InnerClient  string `json:"inner_client"`
			InnerKey     string `json:"inner_key"`
		}
		var b []byte
		decode := func(s string) ([]byte, error) {
			if gateway {
				return base64.RawURLEncoding.DecodeString(s)
			}
			return base64.StdEncoding.DecodeString(s)
		}
		if gateway {
			b, err = decode(strings.TrimPrefix(raw, "ai-secretary:gateway:v1:"))
			if err == nil {
				b, err = inflate(b)
			}
		} else if strings.HasPrefix(raw, "ai-secretary:identity:") {
			b = []byte(strings.TrimPrefix(raw, "ai-secretary:identity:"))
		} else {
			return nil, errors.New("Это не QR подключения AI Секретаря")
		}
		if err != nil {
			return nil, err
		}
		if err = json.Unmarshal(b, &m); err != nil || m.V != 1 {
			return nil, errors.New("Некорректный QR")
		}
		server := m.Server
		if gateway {
			server = m.Gateway
		}
		f = [][]byte{[]byte(server)}
		for _, s := range []string{m.Cert, m.Key} {
			v, e := decode(s)
			if e != nil {
				return nil, e
			}
			f = append(f, v)
		}
		if gateway {
			der, e := decode(m.InnerServer)
			if e != nil {
				return nil, e
			}
			c, e := x509.ParseCertificate(der)
			if e != nil || c.VerifyHostname("secretary.internal") != nil {
				return nil, errors.New("Некорректный внутренний сервер")
			}
			p := sha256.Sum256(der)
			f = append(f, p[:])
			for _, s := range []string{m.InnerClient, m.InnerKey} {
				v, e := decode(s)
				if e != nil {
					return nil, e
				}
				f = append(f, v)
			}
			id.Installation = m.Installation
		}
	}
	id.Server, err = Origin(string(f[0]))
	if err != nil {
		return nil, err
	}
	id.Client, err = pair(f[1], f[2], compact)
	if err != nil {
		return nil, err
	}
	if gateway {
		uris := id.Client.Leaf.URIs
		if len(uris) != 1 || !gatewayURI.MatchString(uris[0].String()) {
			return nil, errors.New("Некорректная роль шлюза")
		}
		parts := strings.Split(uris[0].Path, "/")
		if uris[0].Scheme != "spiffe" || uris[0].Host != "ai-secretary-gateway" || len(parts) != 5 || parts[1] != "installations" || parts[3] != "roles" || parts[4] != "client" || len(parts[2]) != 36 {
			return nil, errors.New("Некорректная роль шлюза")
		}
		if id.Installation != "" && id.Installation != parts[2] {
			return nil, errors.New("UID не совпадает")
		}
		id.Installation = parts[2]
		if len(f[3]) != 32 {
			return nil, errors.New("Некорректный отпечаток")
		}
		id.Pin = append([]byte(nil), f[3]...)
		id.Inner, err = pair(f[4], f[5], compact)
		if err != nil {
			return nil, err
		}
	}
	return id, nil
}
