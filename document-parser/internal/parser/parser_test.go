package parser

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestPythonCompatibility(t *testing.T) {
	var cases []struct {
		File      string
		Limit     int
		Text      string
		Truncated bool
		Metadata  map[string]any
	}
	data, err := os.ReadFile("../../testdata/expected.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	for _, test := range cases {
		t.Run(test.File+"/"+strconv.Itoa(test.Limit), func(t *testing.T) {
			data, err := os.ReadFile("../../testdata/" + test.File)
			if err != nil {
				t.Fatal(err)
			}
			result, err := Extract(context.Background(), data, filepath.Ext(test.File), "", test.Limit)
			if err != nil {
				t.Fatal(err)
			}
			if result.Text != test.Text {
				t.Errorf("text mismatch\ngot  %q\nwant %q", result.Text, test.Text)
			}
			if result.Truncated != test.Truncated {
				t.Errorf("truncated=%v want %v", result.Truncated, test.Truncated)
			}
			metadata, _ := json.Marshal(result.Metadata)
			want, _ := json.Marshal(test.Metadata)
			if !bytes.Equal(metadata, want) {
				t.Errorf("metadata=%s want %s", metadata, want)
			}
			if result.MediaType != "application/octet-stream" {
				t.Fatal(result.MediaType)
			}
		})
	}
}
func TestRejectInvalidDocuments(t *testing.T) {
	for _, suffix := range []string{".pdf", ".docx", ".xlsx", ".txt"} {
		if _, err := Extract(context.Background(), []byte("invalid"), suffix, "", 100); err == nil {
			t.Errorf("accepted %s", suffix)
		}
	}
	for _, name := range []string{"encrypted.pdf", "encrypted-empty.pdf"} {
		data, err := os.ReadFile("../../testdata/" + name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = Extract(context.Background(), data, ".pdf", "", 100); err == nil {
			t.Errorf("accepted %s", name)
		}
	}
}
func TestArchiveAndXMLLimits(t *testing.T) {
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	for _, name := range []string{"[Content_Types].xml", "../document.xml"} {
		file, _ := archive.Create(name)
		file.Write([]byte("<x/>"))
	}
	archive.Close()
	if _, err := openPackage(buffer.Bytes()); err == nil {
		t.Fatal("accepted traversal entry")
	}
	_, err := parseTree(context.Background(), strings.NewReader(strings.Repeat("<x>", 130)+strings.Repeat("</x>", 130)))
	if err == nil {
		t.Fatal("accepted deep XML")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := parseTree(ctx, strings.NewReader("<x/>")); err == nil {
		t.Fatal("ignored cancellation")
	}
}
func TestBoundedUnicode(t *testing.T) {
	text := NewText(4)
	if !text.Add("😀я") {
		t.Fatal("early truncation")
	}
	if text.Add("ёж") {
		t.Fatal("expected truncation")
	}
	if text.String() != "😀я\nё" || !text.Truncated {
		t.Fatalf("%+v", text)
	}
}

func TestRejectExpandedArchive(t *testing.T) {
	var buffer bytes.Buffer
	archive := zip.NewWriter(&buffer)
	file, err := archive.Create("[Content_Types].xml")
	if err != nil {
		t.Fatal(err)
	}
	file.Write([]byte("<Types/>"))
	_, err = archive.CreateRaw(&zip.FileHeader{Name: "word/document.xml", Method: zip.Store, UncompressedSize64: MaxExpandedBytes + 1})
	if err != nil {
		t.Fatal(err)
	}
	if err = archive.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = openPackage(buffer.Bytes()); err == nil {
		t.Fatal("accepted oversized archive")
	}
}
