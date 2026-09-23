package server

import (
	"regexp"
)

var promiseToAskPattern = regexp.MustCompile(`(?i)(?:^|[^\pL])(?:я\s+)?(?:попрошу|попросим|поручу|планирую\s+попросить)(?:$|[^\pL])`)
var directAddresseePattern = regexp.MustCompile(`(?i)(?:^|[^\pL])(?:тебя|тебе|вас|вам)(?:$|[^\pL])`)

// Promising to ask someone later is the sender's next task, even when that
// person is named. A direct second-person request is already an assignment.
func promiseToAskWithoutAddressee(event M, evidence string) bool {
	return str(event, "direction") == "OUTGOING" &&
		promiseToAskPattern.MatchString(evidence) &&
		!directAddresseePattern.MatchString(evidence)
}
