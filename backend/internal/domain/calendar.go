package domain

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

//go:embed holidays.json.gz
var holidayData []byte
var holidays map[string]map[string]bool

func init() {
	reader, e := gzip.NewReader(bytes.NewReader(holidayData))
	if e != nil {
		panic(e)
	}
	defer reader.Close()
	var dates map[string][]string
	if e = json.NewDecoder(reader).Decode(&dates); e != nil {
		panic(e)
	}
	holidays = map[string]map[string]bool{}
	for country, days := range dates {
		set := map[string]bool{}
		for _, d := range days {
			set[d] = true
		}
		holidays[country] = set
	}
}
func WorkingDay(day time.Time, country string, working, nonWorking []string) (bool, error) {
	key := day.Format("2006-01-02")
	for _, d := range working {
		if d == key {
			return true, nil
		}
	}
	for _, d := range nonWorking {
		if d == key {
			return false, nil
		}
	}
	set, ok := holidays[strings.ToUpper(country)]
	if !ok {
		return false, fmt.Errorf("unsupported holiday country %q", country)
	}
	if day.Year() < 1970 || day.Year() > 2100 {
		return false, fmt.Errorf("holiday data covers 1970–2100")
	}
	return day.Weekday() != time.Saturday && day.Weekday() != time.Sunday && !set[key], nil
}
func NormalizeDue(due time.Time, location *time.Location, country string, working, nonWorking []string) (time.Time, error) {
	due = due.In(location)
	for i := 0; i < 3660; i++ {
		ok, e := WorkingDay(due, country, working, nonWorking)
		if e != nil {
			return due, e
		}
		if ok {
			return due, nil
		}
		due = due.AddDate(0, 0, -1)
	}
	return due, fmt.Errorf("no working day found")
}
