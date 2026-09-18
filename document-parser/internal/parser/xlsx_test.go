package parser

import (
	"context"
	"strings"
	"testing"

	"github.com/xuri/excelize/v2"
)

func TestSharedStringsAndRichText(t *testing.T) {
	workbook := excelize.NewFile()
	defer workbook.Close()
	if err := workbook.SetCellStr("Sheet1", "A1", "Кириллица 😀"); err != nil {
		t.Fatal(err)
	}
	if err := workbook.SetCellRichText("Sheet1", "B1", []excelize.RichTextRun{{Text: "первая "}, {Text: "вторая", Font: &excelize.Font{Bold: true}}}); err != nil {
		t.Fatal(err)
	}
	workbook.SetCellValue("Sheet1", "A2", 12)
	workbook.MergeCell("Sheet1", "A2", "C2")
	buffer, err := workbook.WriteToBuffer()
	if err != nil {
		t.Fatal(err)
	}
	result, err := Extract(context.Background(), buffer.Bytes(), ".xlsx", "", 10000)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(result.Text, "Кириллица 😀\tпервая вторая") || !strings.HasSuffix(result.Text, "\n12") {
		t.Fatal(result.Text)
	}
	if result.Metadata["non_empty_cells"] != 3 {
		t.Fatal(result.Metadata)
	}
}
