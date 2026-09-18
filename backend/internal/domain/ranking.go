package domain

import (
	"math"
	"strings"
	"time"
)

func Rank(priority, status string, due *time.Time, now time.Time) (float64, []string) {
	score := map[string]float64{"LOW": 100, "NORMAL": 200, "HIGH": 300, "CRITICAL": 400}[priority]
	reasons := []string{"приоритет: " + strings.ToLower(priority)}
	if due == nil {
		reasons = append(reasons, "срок не указан")
	} else {
		seconds := due.Sub(now).Seconds()
		hours := seconds / 3600
		switch {
		case seconds < 0:
			score += 600 + math.Min(-seconds/86400, 30)*10
			reasons = append(reasons, "задача просрочена")
		case hours <= 2:
			score += 500
			reasons = append(reasons, "до срока меньше двух часов")
		case hours <= 8:
			score += 420
			reasons = append(reasons, "срок сегодня")
		case hours <= 24:
			score += 340
			reasons = append(reasons, "срок в течение суток")
		case hours <= 72:
			score += 240
			reasons = append(reasons, "срок в ближайшие три дня")
		case hours <= 168:
			score += 140
			reasons = append(reasons, "срок в течение недели")
		}
	}
	if status == "POSSIBLY_COMPLETED" {
		score -= 50
		reasons = append(reasons, "ожидает подтверждения выполнения")
	} else if status == "IN_PROGRESS" {
		score += 20
		reasons = append(reasons, "задача в работе")
	}
	return math.Round(score*100) / 100, reasons
}
func Active(status string) bool {
	return status == "NEW" || status == "IN_PROGRESS" || status == "POSSIBLY_COMPLETED"
}
