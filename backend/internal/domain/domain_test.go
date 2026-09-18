package domain

import (
	"testing"
	"time"
)

func TestCalendarOverridesAndDeadlineTime(t *testing.T) {
	loc, err := time.LoadLocation("Europe/Moscow")
	if err != nil {
		t.Fatal(err)
	}
	saturday := time.Date(2026, 9, 19, 15, 30, 0, 0, loc)
	normalized, err := NormalizeDue(saturday, loc, "RU", nil, nil)
	if err != nil || normalized.Day() != 18 || normalized.Hour() != 15 || normalized.Minute() != 30 {
		t.Fatal(normalized, err)
	}
	normalized, err = NormalizeDue(saturday, loc, "RU", []string{"2026-09-19"}, []string{"2026-09-19"})
	if err != nil || normalized.Day() != 19 {
		t.Fatal(normalized, err)
	}
	if working, err := WorkingDay(time.Date(2026, 1, 1, 0, 0, 0, 0, loc), "RU", nil, nil); err != nil || working {
		t.Fatal(working, err)
	}
}
func TestRanking(t *testing.T) {
	now := time.Date(2026, 9, 17, 10, 0, 0, 0, time.UTC)
	overdue := now.Add(-24 * time.Hour)
	future := now.Add(10 * 24 * time.Hour)
	a, _ := Rank("NORMAL", "NEW", &overdue, now)
	b, _ := Rank("NORMAL", "NEW", &future, now)
	if a != 810 || b != 200 {
		t.Fatal(a, b)
	}
	c, _ := Rank("NORMAL", "POSSIBLY_COMPLETED", nil, now)
	if c != 150 {
		t.Fatal(c)
	}
}
