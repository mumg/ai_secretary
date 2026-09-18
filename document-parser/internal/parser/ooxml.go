package parser

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"path"
	"strconv"
	"strings"
)

type packageZIP struct{ files map[string]*zip.File }

func openPackage(data []byte) (*packageZIP, error) {
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, errors.New("invalid Office archive")
	}
	if len(reader.File) > 10000 {
		return nil, errors.New("too many Office archive entries")
	}
	files := map[string]*zip.File{}
	var expanded uint64
	for _, file := range reader.File {
		if file.FileInfo().IsDir() {
			continue
		}
		if file.Flags&1 != 0 {
			return nil, errors.New("encrypted Office archive is not supported")
		}
		if file.UncompressedSize64 > MaxExpandedBytes || expanded > MaxExpandedBytes-file.UncompressedSize64 {
			return nil, errors.New("Office archive exceeds extraction size limit")
		}
		expanded += file.UncompressedSize64
		name := path.Clean(file.Name)
		if name != file.Name || strings.HasPrefix(name, "/") || strings.HasPrefix(name, "../") || strings.Contains(name, "\\") {
			return nil, errors.New("invalid Office archive path")
		}
		if files[name] != nil {
			return nil, errors.New("duplicate Office archive entry")
		}
		files[name] = file
	}
	if files["[Content_Types].xml"] == nil {
		return nil, errors.New("missing Office content types")
	}
	return &packageZIP{files: files}, nil
}
func (p *packageZIP) reader(name string) (io.ReadCloser, error) {
	file := p.files[name]
	if file == nil {
		return nil, fmt.Errorf("missing Office part %s", name)
	}
	return file.Open()
}
func (p *packageZIP) decode(name string, out any) error {
	r, err := p.reader(name)
	if err != nil {
		return err
	}
	defer r.Close()
	return xml.NewDecoder(io.LimitReader(r, MaxExpandedBytes+1)).Decode(out)
}

type xmlNode struct {
	Name     xml.Name
	Attrs    []xml.Attr
	Text     string
	Children []*xmlNode
}

func (n *xmlNode) attr(name string) string {
	for _, a := range n.Attrs {
		if a.Name.Local == name {
			return a.Value
		}
	}
	return ""
}
func (n *xmlNode) child(name string) *xmlNode {
	for _, c := range n.Children {
		if c.Name.Local == name {
			return c
		}
	}
	return nil
}
func parseTree(ctx context.Context, r io.Reader) (*xmlNode, error) {
	root := &xmlNode{}
	stack := []*xmlNode{root}
	decoder := xml.NewDecoder(io.LimitReader(r, MaxExpandedBytes+1))
	nodes := 0
	for {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, errors.New("invalid document XML")
		}
		switch t := token.(type) {
		case xml.StartElement:
			nodes++
			if len(stack) > 128 || nodes > 1000000 {
				return nil, errors.New("document XML complexity limit exceeded")
			}
			node := &xmlNode{Name: t.Name, Attrs: t.Attr}
			parent := stack[len(stack)-1]
			parent.Children = append(parent.Children, node)
			stack = append(stack, node)
		case xml.CharData:
			node := stack[len(stack)-1]
			node.Text += string(t)
		case xml.EndElement:
			stack = stack[:len(stack)-1]
		}
	}
	if len(root.Children) != 1 {
		return nil, errors.New("invalid document XML root")
	}
	return root.Children[0], nil
}
func wordText(n *xmlNode) string {
	switch n.Name.Local {
	case "t":
		return n.Text
	case "tab":
		return "\t"
	case "br", "cr":
		if kind := n.attr("type"); kind == "" || kind == "textWrapping" {
			return "\n"
		}
		return ""
	case "del", "instrText", "delText", "tbl":
		return ""
	}
	var b strings.Builder
	for _, child := range n.Children {
		b.WriteString(wordText(child))
	}
	return b.String()
}
func cellText(cell *xmlNode) string {
	parts := []string{}
	for _, child := range cell.Children {
		if child.Name.Local == "p" {
			parts = append(parts, wordText(child))
		}
	}
	return strings.Join(parts, "\n")
}
func cellSpan(cell *xmlNode) int {
	if props := cell.child("tcPr"); props != nil {
		if span := props.child("gridSpan"); span != nil {
			n, err := strconv.Atoi(span.attr("val"))
			if err == nil && n > 0 && n <= 16384 {
				return n
			}
		}
	}
	return 1
}
func extractDOCX(ctx context.Context, data []byte, out *BoundedText) (map[string]any, error) {
	archive, err := openPackage(data)
	if err != nil {
		return nil, err
	}
	r, err := archive.reader("word/document.xml")
	if err != nil {
		return nil, err
	}
	defer r.Close()
	document, err := parseTree(ctx, r)
	if err != nil {
		return nil, err
	}
	if document.Name.Local != "document" {
		return nil, errors.New("invalid Word document")
	}
	body := document.child("body")
	if body == nil {
		return nil, errors.New("missing Word document body")
	}
	paragraphs, tables := []*xmlNode{}, []*xmlNode{}
	for _, child := range body.Children {
		switch child.Name.Local {
		case "p":
			paragraphs = append(paragraphs, child)
		case "tbl":
			tables = append(tables, child)
		}
	}
	for _, paragraph := range paragraphs {
		if !out.Add(wordText(paragraph)) {
			break
		}
	}
	tableCount := 0
	for _, table := range tables {
		tableCount++
		if !out.Add(fmt.Sprintf("[Таблица %d]", tableCount)) {
			break
		}
		previous := map[int]string{}
		for _, row := range table.Children {
			if row.Name.Local != "tr" {
				continue
			}
			if err := ctx.Err(); err != nil {
				return nil, err
			}
			values := []string{}
			current := map[int]string{}
			column := 0
			if props := row.child("trPr"); props != nil {
				if before := props.child("gridBefore"); before != nil {
					column, _ = strconv.Atoi(before.attr("val"))
				}
			}
			for _, cell := range row.Children {
				if cell.Name.Local != "tc" {
					continue
				}
				value := cellText(cell)
				span := cellSpan(cell)
				if props := cell.child("tcPr"); props != nil {
					if merge := props.child("vMerge"); merge != nil && merge.attr("val") != "restart" {
						value = previous[column]
					}
				}
				for i := 0; i < span; i++ {
					values = append(values, value)
					current[column] = value
					column++
				}
			}
			previous = current
			if !out.Add(strings.Join(values, "\t")) {
				break
			}
		}
		if out.Truncated {
			break
		}
	}
	return map[string]any{"paragraphs": len(paragraphs), "tables": tableCount}, nil
}
