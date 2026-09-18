package parser

import (
	"bytes"
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"math"
	"path"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/xuri/excelize/v2"
)

type workbookXML struct {
	Sheets []struct {
		Name string `xml:"name,attr"`
		ID   string `xml:"id,attr"`
	} `xml:"sheets>sheet"`
}
type relationshipsXML struct {
	Items []struct {
		ID     string `xml:"Id,attr"`
		Target string `xml:"Target,attr"`
		Type   string `xml:"Type,attr"`
		Mode   string `xml:"TargetMode,attr"`
	} `xml:"Relationship"`
}
type sheetCell struct {
	Ref     string  `xml:"r,attr"`
	Type    string  `xml:"t,attr"`
	Value   *string `xml:"v"`
	Formula *struct {
		Text string `xml:",chardata"`
	} `xml:"f"`
	Inline *struct{} `xml:"is"`
}
type sheetRow struct {
	Cells []sheetCell `xml:"c"`
}

func extractXLSX(ctx context.Context, data []byte, out *BoundedText) (map[string]any, error) {
	archive, err := openPackage(data)
	if err != nil {
		return nil, err
	}
	var workbook workbookXML
	var relationships relationshipsXML
	if err = archive.decode("xl/workbook.xml", &workbook); err != nil {
		return nil, err
	}
	if err = archive.decode("xl/_rels/workbook.xml.rels", &relationships); err != nil {
		return nil, err
	}
	file, err := excelize.OpenReader(bytes.NewReader(data), excelize.Options{RawCellValue: true, UnzipSizeLimit: MaxExpandedBytes, UnzipXMLSizeLimit: 8 * 1024 * 1024})
	if err != nil {
		return nil, errors.New("invalid Excel workbook")
	}
	defer file.Close()
	props, err := file.GetWorkbookProps()
	if err != nil {
		return nil, err
	}
	use1904 := props.Date1904 != nil && *props.Date1904
	targets := map[string]string{}
	for _, rel := range relationships.Items {
		if !strings.HasSuffix(rel.Type, "/worksheet") {
			continue
		}
		if rel.Mode == "External" {
			return nil, errors.New("external worksheet is not supported")
		}
		target := path.Clean(path.Join("xl", rel.Target))
		if strings.HasPrefix(rel.Target, "/") {
			target = strings.TrimPrefix(path.Clean(rel.Target), "/")
		}
		if !strings.HasPrefix(target, "xl/") {
			return nil, errors.New("invalid worksheet path")
		}
		targets[rel.ID] = target
	}
	count := 0
	for _, sheet := range workbook.Sheets {
		target, ok := targets[sheet.ID]
		if !ok {
			continue
		}
		if !out.Add("[Лист: " + sheet.Name + "]") {
			break
		}
		err = func() error {
			reader, err := archive.reader(target)
			if err != nil {
				return err
			}
			defer reader.Close()
			decoder := xml.NewDecoder(io.LimitReader(reader, MaxExpandedBytes+1))
			for {
				if err := ctx.Err(); err != nil {
					return err
				}
				token, err := decoder.Token()
				if err == io.EOF {
					return nil
				}
				if err != nil {
					return errors.New("invalid worksheet XML")
				}
				start, ok := token.(xml.StartElement)
				if !ok || start.Name.Local != "row" {
					continue
				}
				var row sheetRow
				if err := decoder.DecodeElement(&row, &start); err != nil {
					return errors.New("invalid worksheet row")
				}
				values := []string{}
				for _, cell := range row.Cells {
					if cell.Value == nil && cell.Formula == nil && cell.Inline == nil {
						continue
					}
					value, err := xlsxValue(file, sheet.Name, cell, use1904)
					if err != nil {
						return err
					}
					values = append(values, value)
				}
				count += len(values)
				if len(values) > 0 && !out.Add(strings.Join(values, "\t")) {
					return nil
				}
			}
		}()
		if err != nil {
			return nil, err
		}
		if out.Truncated {
			break
		}
	}
	return map[string]any{"sheets": len(workbook.Sheets), "non_empty_cells": count}, nil
}
func xlsxValue(file *excelize.File, sheet string, cell sheetCell, use1904 bool) (string, error) {
	if _, _, err := excelize.CellNameToCoordinates(cell.Ref); err != nil {
		return "", errors.New("invalid worksheet cell reference")
	}
	if cell.Formula != nil {
		formula, err := file.GetCellFormula(sheet, cell.Ref)
		if err != nil {
			return "", err
		}
		return "=" + formula, nil
	}
	raw := ""
	if cell.Value != nil {
		raw = *cell.Value
	}
	switch cell.Type {
	case "b":
		if raw == "0" {
			return "False", nil
		}
		return "True", nil
	case "e", "str":
		return raw, nil
	case "s", "inlineStr":
		return file.GetCellValue(sheet, cell.Ref, excelize.Options{RawCellValue: true})
	case "d":
		for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05.999999999", "2006-01-02"} {
			if date, err := time.Parse(layout, raw); err == nil {
				return pythonDate(date, false), nil
			}
		}
		return "", errors.New("invalid Excel date")
	}
	number, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return "", errors.New("invalid Excel number")
	}
	if math.IsInf(number, 0) || math.IsNaN(number) {
		return "", errors.New("invalid Excel number")
	}
	styleID, err := file.GetCellStyle(sheet, cell.Ref)
	if err != nil {
		return "", err
	}
	style, err := file.GetStyle(styleID)
	if err != nil {
		return "", err
	}
	dateFormat, elapsed := false, false
	if style != nil {
		dateFormat = (style.NumFmt >= 14 && style.NumFmt <= 22) || (style.NumFmt >= 45 && style.NumFmt <= 47)
		elapsed = style.NumFmt == 46
		if style.CustomNumFmt != nil {
			format := strings.ToLower(strings.Split(*style.CustomNumFmt, ";")[0])
			elapsed = elapsedFormat.MatchString(format)
			format = ignoredFormat.ReplaceAllString(format, "")
			dateFormat = dateLetters.MatchString(format) || elapsed
		}
	}
	if elapsed {
		return pythonDuration(number), nil
	}
	if dateFormat {
		// Match openpyxl's millisecond rounding and Excel's fictitious 1900 leap day.
		if number < -693593 || number > 2958465 {
			return "", errors.New("Excel date is out of range")
		}
		day := math.Floor(number)
		milliseconds := int64(math.Round((number - day) * 86400000))
		epoch := time.Date(1899, 12, 30, 0, 0, 0, 0, time.UTC)
		if use1904 {
			epoch = time.Date(1904, 1, 1, 0, 0, 0, 0, time.UTC)
		} else if number > 0 && number < 60 {
			day++
		}
		date := epoch.AddDate(0, 0, int(day)).Add(time.Duration(milliseconds) * time.Millisecond)
		return pythonDate(date, number >= 0 && number < 1 && milliseconds < 86400000), nil
	}
	if strings.ContainsAny(raw, ".eE") {
		value := strconv.FormatFloat(number, 'g', -1, 64)
		if !strings.ContainsAny(value, ".eE") {
			value += ".0"
		}
		return value, nil
	}
	return raw, nil
}

var ignoredFormat = regexp.MustCompile(`"[^"]*"|\\.|\[[^\]]*\]`)
var dateLetters = regexp.MustCompile(`[ymdhs]`)
var elapsedFormat = regexp.MustCompile(`\[(h+|m+|s+)\]`)

func pythonDate(date time.Time, timeOnly bool) string {
	layout := "2006-01-02 15:04:05"
	if timeOnly {
		layout = "15:04:05"
	}
	value := date.Format(layout)
	micro := date.Nanosecond() / 1000
	if micro != 0 {
		value += fmt.Sprintf(".%06d", micro)
	}
	return value
}
func pythonDuration(days float64) string {
	milliseconds := int64(math.Round(days * 86400000))
	wholeDays := int64(math.Floor(float64(milliseconds) / 86400000))
	remainder := milliseconds - wholeDays*86400000
	hours := remainder / 3600000
	minutes := (remainder / 60000) % 60
	seconds := (remainder / 1000) % 60
	value := fmt.Sprintf("%d:%02d:%02d", hours, minutes, seconds)
	if ms := remainder % 1000; ms != 0 {
		value += fmt.Sprintf(".%06d", ms*1000)
	}
	if wholeDays != 0 {
		unit := "days"
		if wholeDays == 1 || wholeDays == -1 {
			unit = "day"
		}
		value = fmt.Sprintf("%d %s, %s", wholeDays, unit, value)
	}
	return value
}
