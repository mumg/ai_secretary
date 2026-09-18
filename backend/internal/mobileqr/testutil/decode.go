// Package testutil contains the independent test reader for the mobile QR format.
package testutil

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"fmt"
	"io"
	"strings"
)

func Decode(payload, prefix string, count int) ([][]byte, error) {
	if !strings.HasPrefix(payload, prefix) {
		return nil, fmt.Errorf("prefix")
	}
	text := strings.TrimPrefix(payload, prefix)
	const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
	var compressed []byte
	for len(text) > 0 {
		n := 3
		if len(text) < n {
			n = len(text)
		}
		if n == 1 {
			return nil, fmt.Errorf("base45 length")
		}
		value, mult := 0, 1
		for _, c := range text[:n] {
			d := strings.IndexRune(alphabet, c)
			if d < 0 {
				return nil, fmt.Errorf("base45 digit")
			}
			value += d * mult
			mult *= 45
		}
		if (n == 3 && value > 65535) || (n == 2 && value > 255) {
			return nil, fmt.Errorf("base45 range")
		}
		if n == 3 {
			compressed = append(compressed, byte(value>>8))
		}
		compressed = append(compressed, byte(value))
		text = text[n:]
	}
	z, e := zlib.NewReader(bytes.NewReader(compressed))
	if e != nil {
		return nil, e
	}
	defer z.Close()
	data, e := io.ReadAll(io.LimitReader(z, 16385))
	if e != nil || len(data) > 16384 {
		return nil, fmt.Errorf("inflate")
	}
	var fields [][]byte
	for len(data) > 0 {
		if len(data) < 2 {
			return nil, fmt.Errorf("field header")
		}
		n := int(binary.BigEndian.Uint16(data))
		data = data[2:]
		if n == 0 || n > 4096 || n > len(data) {
			return nil, fmt.Errorf("field length")
		}
		fields = append(fields, data[:n])
		data = data[n:]
	}
	if len(fields) != count {
		return nil, fmt.Errorf("field count")
	}
	return fields, nil
}
