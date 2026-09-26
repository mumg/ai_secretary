package server

import (
	"regexp"
	"strings"
)

var statusOnlyMailPrefix = regexp.MustCompile(`(?i)^\s*(?:fyi\b|для\s+информации\b|на\s+данный\s+момент\s+задачи\s+следующие\s*:|текущие\s+задачи\s*:)`)
var explicitMailAssignment = regexp.MustCompile(`(?i)(?:^|[^\pL])(?:прошу|пожалуйста|поручаю|сделай|сделайте|выполни|выполните)(?:$|[^\pL])`)

// A status list or FYI forward describes work; bare infinitives in that list
// do not assign it to the recipient. A direct request in the sender's own text
// still permits creating a delegation.
func statusOnlyOutgoingMail(body string) bool {
	if !statusOnlyMailPrefix.MatchString(body) {
		return false
	}
	own := body
	for _, separator := range []string{"\nОт:", "\nFrom:", "\nС уважением,"} {
		if index := strings.Index(own, separator); index >= 0 {
			own = own[:index]
		}
	}
	return !explicitMailAssignment.MatchString(own)
}
