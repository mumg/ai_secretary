package server

import (
	"net/mail"
	"regexp"
	"sort"
	"strings"
)

type stringSet map[string]bool

func setOf(values []string) stringSet {
	s := stringSet{}
	for _, v := range values {
		s[v] = true
	}
	return s
}
func (a stringSet) overlaps(b stringSet) bool {
	for k := range a {
		if b[k] {
			return true
		}
	}
	return false
}
func (a stringSet) subset(b stringSet) bool {
	for k := range a {
		if !b[k] {
			return false
		}
	}
	return true
}
func (a stringSet) sorted() []string {
	out := []string{}
	for k := range a {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func nameWords(s string) stringSet {
	return setOf(regexp.MustCompile(`[\pL\pN_-]+`).FindAllString(normalizeName(s), -1))
}
func personAddresses(p M) stringSet {
	return setOf(append(parseAddresses(str(p, "address")), parseAddresses(str(p, "email"))...))
}
func personIsUser(p M, names []string, addresses stringSet) bool {
	if a := personAddresses(p); len(a) > 0 {
		return a.overlaps(addresses)
	}
	words := nameWords(str(p, "name"))
	if len(words) == 0 {
		return false
	}
	for _, name := range names {
		full := nameWords(name)
		if len(words) == len(full) && words.subset(full) {
			return true
		}
	}
	return false
}
func authorPeople(author string) []M {
	if author == "" {
		return nil
	}
	if !strings.Contains(author, "@") {
		return []M{{"name": author, "role": "author"}}
	}
	result := []M{}
	people, _ := mail.ParseAddressList(author)
	for _, p := range people {
		result = append(result, M{"name": p.Name, "address": p.Address, "role": "author"})
	}
	return result
}
func assignmentSignals(event M, names, addresses []string, previous []M) M {
	user := setOf(addresses)
	tokens := stringSet{}
	for _, name := range names {
		for word := range nameWords(name) {
			tokens[word] = true
		}
	}
	current := roster(event)
	people := append(append([]M{}, current...), authorPeople(str(event, "author"))...)
	for _, p := range previous {
		people = append(people, roster(p)...)
		if p["occurred_at"] != nil {
			people = append(people, authorPeople(str(p, "author"))...)
		}
	}
	mailboxKey := func(a stringSet) string {
		if a.overlaps(user) {
			return "user"
		}
		return "email:" + a.sorted()[0]
	}
	personKey := func(p M) string {
		if a := personAddresses(p); len(a) > 0 {
			return mailboxKey(a)
		}
		if id := str(p, "external_id"); id != "" {
			return "id:" + id
		}
		same := stringSet{}
		words := nameWords(str(p, "name"))
		for _, other := range people {
			a := personAddresses(other)
			o := nameWords(str(other, "name"))
			if len(a) > 0 && len(o) == len(words) && words.subset(o) {
				same[mailboxKey(a)] = true
			}
		}
		if len(same) == 1 {
			return same.sorted()[0]
		}
		return "name:" + strings.Join(words.sorted(), " ")
	}
	known, potential := stringSet{}, stringSet{}
	foreign := false
	for _, p := range people {
		if personIsUser(p, names, user) {
			known[personKey(p)] = true
		}
		words := nameWords(str(p, "name"))
		a := personAddresses(p)
		if len(a) == 0 && len(words) > 0 && words.subset(tokens) {
			potential[personKey(p)] = true
		}
		foreign = foreign || (len(a) > 0 && !a.overlaps(user) && words.overlaps(tokens))
	}
	if len(potential) == 1 && !foreign {
		for k := range potential {
			known[k] = true
		}
	}
	knownNames := len(known)
	if known["user"] {
		knownNames--
	}
	nameOnlyAmbiguous := knownNames > 1
	competitors := []M{}
	for _, p := range people {
		if !known[personKey(p)] || nameOnlyAmbiguous {
			competitors = append(competitors, p)
		}
	}
	ambiguous, unique := stringSet{}, stringSet{}
	for word := range tokens {
		for _, p := range competitors {
			if nameWords(str(p, "name"))[word] {
				ambiguous[word] = true
			}
		}
		if !ambiguous[word] {
			unique[word] = true
		}
	}
	for _, name := range names {
		words := nameWords(name)
		if len(words) < 2 {
			continue
		}
		competing := false
		for _, p := range competitors {
			competing = competing || words.subset(nameWords(str(p, "name")))
		}
		if !competing {
			unique[name] = true
		}
	}
	author := setOf(parseAddresses(str(event, "author")))
	isAuthor := event["direction"] == "OUTGOING" || author.overlaps(user)
	recipientKeys := stringSet{}
	isRecipient := false
	for _, p := range current {
		role := strings.ToLower(str(p, "role"))
		if role == "from" || role == "sender" || role == "author" {
			continue
		}
		a := personAddresses(p)
		if role == "" && len(a) > 0 && a.subset(author) {
			continue
		}
		words := nameWords(str(p, "name"))
		if len(a) == 0 && len(words) == 0 {
			continue
		}
		recipientKeys[personKey(p)] = true
		isRecipient = isRecipient || personIsUser(p, names, user) || (len(a) == 0 && words.overlaps(tokens))
	}
	mentioned, namesInBody := false, stringSet{}
	body := str(event, "body")
	for word := range tokens {
		if hasName(body, word) {
			namesInBody[word] = true
		}
	}
	local := stringSet{}
	for a := range user {
		local[strings.Split(a, "@")[0]] = true
	}
	mentions := stringSet{}
	r := must(compileLinkPattern(`(?<!\w)@([\w.-]+)`))
	for m := must(r.FindStringMatch(strings.ToLower(body))); m != nil; {
		mentions[m.GroupByNumber(1).String()] = true
		m = must(r.FindNextMatch(m))
	}
	ambiguousMentions := stringSet{}
	for mention := range mentions {
		mentioned = mentioned || tokens[mention] || local[mention]
		if ambiguous[mention] {
			ambiguousMentions[mention] = true
		}
		if local[mention] {
			for _, p := range people {
				for a := range personAddresses(p) {
					if strings.Split(a, "@")[0] == mention && !user[a] {
						ambiguousMentions[mention] = true
					}
				}
			}
		}
	}
	nameAmbiguous := namesInBody.overlaps(ambiguous) || len(ambiguousMentions) > 0 || nameOnlyAmbiguous
	for word := range ambiguousMentions {
		ambiguous[word] = true
	}
	return M{"eligible": mentioned || isRecipient || isAuthor, "directly_mentioned": mentioned, "user_is_recipient": isRecipient, "user_is_author": isAuthor, "sole_recipient": isRecipient && len(recipientKeys) == 1 && !nameOnlyAmbiguous, "addressed_by_name": isRecipient && len(namesInBody) > 0, "name_ambiguous": nameAmbiguous, "ambiguous_names": ambiguous.sorted(), "unambiguous_names": unique.sorted()}
}
