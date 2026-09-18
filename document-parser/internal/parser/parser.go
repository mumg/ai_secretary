// Package parser extracts local text without running macros, formulas, or fetching links.
package parser

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/ledongthuc/pdf"
)

const DefaultMaxBytes = 25 * 1024 * 1024
const DefaultMaxCharacters = 100000
const MaxExpandedBytes = 128 * 1024 * 1024

type Result struct {
	Text      string         `json:"text"`
	MediaType string         `json:"media_type"`
	Truncated bool           `json:"truncated"`
	Metadata  map[string]any `json:"metadata"`
}
type BoundedText struct {
	limit, length int
	parts         []string
	Truncated     bool
}

func NewText(limit int) *BoundedText { return &BoundedText{limit: limit} }
func (b *BoundedText) Add(value string) bool {
	if b.Truncated {
		return false
	}
	value = strings.TrimSpace(value)
	if value == "" {
		return true
	}
	available := b.limit - b.length
	if available <= 0 {
		b.Truncated = true
		return false
	}
	length := utf8.RuneCountInString(value)
	if length > available {
		value = string([]rune(value)[:available])
		b.Truncated = true
		length = available
	}
	b.parts = append(b.parts, value)
	b.length += length + 1
	return !b.Truncated
}
func (b *BoundedText) String() string { return strings.Join(b.parts, "\n") }
func Extract(ctx context.Context, data []byte, suffix, mediaType string, limit int) (result Result, err error) {
	defer func() {
		if recover() != nil {
			result = Result{}
			err = errors.New("invalid document")
		}
	}()
	if limit < 1 || limit > 10000000 {
		return result, errors.New("invalid character limit")
	}
	text := NewText(limit)
	var metadata map[string]any
	switch suffix {
	case ".pdf":
		metadata, err = extractPDF(ctx, data, text)
	case ".docx":
		metadata, err = extractDOCX(ctx, data, text)
	case ".xlsx":
		metadata, err = extractXLSX(ctx, data, text)
	default:
		return result, errors.New("unsupported document type")
	}
	if err != nil {
		return result, err
	}
	if mediaType == "" {
		mediaType = "application/octet-stream"
	}
	return Result{Text: text.String(), MediaType: mediaType, Truncated: text.Truncated, Metadata: metadata}, nil
}
func extractPDF(ctx context.Context, data []byte, out *BoundedText) (map[string]any, error) {
	reader, err := pdf.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, errors.New("invalid or encrypted PDF")
	}
	if !reader.Trailer().Key("Encrypt").IsNull() {
		return nil, errors.New("password-protected PDF is not supported")
	}
	pages := reader.NumPage()
	if pages < 0 || pages > 100000 {
		return nil, errors.New("PDF page limit exceeded")
	}
	for number := 1; number <= pages; number++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		if !out.Add(fmt.Sprintf("[Страница %d]", number)) {
			break
		}
		page := reader.Page(number)
		if page.V.IsNull() {
			return nil, errors.New("invalid PDF page")
		}
		text, err := pdfText(ctx, page)
		if err != nil {
			return nil, errors.New("unable to extract PDF text")
		}
		if !out.Add(text) {
			break
		}
	}
	return map[string]any{"pages": pages}, nil
}
