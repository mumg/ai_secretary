package server

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/Azure/go-ntlmssp"
	"github.com/icholy/digest"
)

type xmlNode struct {
	Name     string
	Attr     map[string]string
	Text     string
	Children []*xmlNode
}

func parseXML(data []byte) (*xmlNode, error) {
	decoder := xml.NewDecoder(bytes.NewReader(data))
	root := &xmlNode{}
	stack := []*xmlNode{root}
	for {
		token, e := decoder.Token()
		if e == io.EOF {
			break
		}
		if e != nil {
			return nil, e
		}
		switch t := token.(type) {
		case xml.StartElement:
			n := &xmlNode{Name: t.Name.Local, Attr: map[string]string{}}
			for _, a := range t.Attr {
				n.Attr[a.Name.Local] = a.Value
			}
			parent := stack[len(stack)-1]
			parent.Children = append(parent.Children, n)
			stack = append(stack, n)
		case xml.CharData:
			stack[len(stack)-1].Text += string(t)
		case xml.EndElement:
			stack = stack[:len(stack)-1]
		}
	}
	return root, nil
}
func (n *xmlNode) all(name string) []*xmlNode {
	out := []*xmlNode{}
	if n.Name == name {
		out = append(out, n)
	}
	for _, c := range n.Children {
		out = append(out, c.all(name)...)
	}
	return out
}
func (n *xmlNode) first(name string) *xmlNode {
	if n.Name == name {
		return n
	}
	for _, c := range n.Children {
		if found := c.first(name); found.Name != "" {
			return found
		}
	}
	return &xmlNode{Attr: map[string]string{}}
}
func esc(s string) string {
	var b bytes.Buffer
	check(xml.EscapeText(&b, []byte(s)))
	return b.String()
}
func (q *request) ews(source M, operation string) *xmlNode {
	settings := obj(source, "settings")
	password, _ := q.sourceTokens(source)
	var transport http.RoundTripper = http.DefaultTransport
	auth := strings.ToLower(str(settings, "auth_type"))
	if auth == "" || auth == "ntlm" {
		transport = ntlmssp.Negotiator{RoundTripper: http.DefaultTransport}
	} else if auth == "digest" {
		transport = &digest.Transport{Username: str(settings, "username"), Password: password}
	} else if auth != "basic" && auth != "noauth" {
		panic(fmt.Errorf("unsupported Exchange auth_type"))
	}
	envelope := `<?xml version="1.0" encoding="utf-8"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages" xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"><s:Header><t:RequestServerVersion Version="Exchange2013_SP1"/></s:Header><s:Body>` + operation + `</s:Body></s:Envelope>`
	req := must(http.NewRequestWithContext(q.Context, "POST", str(settings, "ews_url"), strings.NewReader(envelope)))
	req.Header.Set("Content-Type", "text/xml; charset=utf-8")
	if auth != "noauth" {
		req.SetBasicAuth(str(settings, "username"), password)
	}
	client := http.Client{Transport: transport, Timeout: 90 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response := must(client.Do(req))
	defer response.Body.Close()
	if response.StatusCode != 200 {
		panic(fmt.Errorf("Exchange HTTP %d", response.StatusCode))
	}
	root := must(parseXML(must(io.ReadAll(io.LimitReader(response.Body, 64<<20)))))
	for _, code := range root.all("ResponseCode") {
		if code.Text != "NoError" {
			panic(fmt.Errorf("Exchange error %s", code.Text))
		}
	}
	return root
}
func ewsFolder(source M, name string) string {
	return `<t:DistinguishedFolderId Id="` + esc(name) + `"><t:Mailbox><t:EmailAddress>` + esc(str(obj(source, "settings"), "primary_smtp_address")) + `</t:EmailAddress></t:Mailbox></t:DistinguishedFolderId>`
}
func (q *request) exchangeSync(source M, testOnly bool) int {
	if testOnly {
		q.ews(source, `<m:GetFolder><m:FolderShape><t:BaseShape>IdOnly</t:BaseShape></m:FolderShape><m:FolderIds>`+ewsFolder(source, "inbox")+ewsFolder(source, "sentitems")+`</m:FolderIds></m:GetFolder>`)
		return 0
	}
	count := 0
	sourceID := str(source, "id")
	settings := q.settings()
	for _, spec := range [][3]string{{"inbox", "INCOMING", "item:DateTimeReceived"}, {"sentitems", "OUTGOING", "item:DateTimeSent"}} {
		folder, cursorFolder := spec[0], spec[0]
		if folder == "sentitems" {
			cursorFolder = "sent"
		}
		key := "exchange_time:" + cursorFolder
		since := time.Now().UTC().AddDate(0, 0, -int(num(obj(settings, "communication_sources"), "initial_sync_days")))
		if previous := timestamp(q.cursor(sourceID, key)); previous != nil {
			since = previous.Add(-5 * time.Minute)
		}
		latest := since
		for offset := 0; offset < 100000; offset += 100 {
			request := `<m:FindItem Traversal="Shallow"><m:ItemShape><t:BaseShape>IdOnly</t:BaseShape></m:ItemShape><m:IndexedPageItemView MaxEntriesReturned="100" Offset="` + strconv.Itoa(offset) + `" BasePoint="Beginning"/><m:Restriction><t:IsGreaterThanOrEqualTo><t:FieldURI FieldURI="` + spec[2] + `"/><t:FieldURIOrConstant><t:Constant Value="` + since.Format(time.RFC3339) + `"/></t:FieldURIOrConstant></t:IsGreaterThanOrEqualTo></m:Restriction><m:SortOrder><t:FieldOrder Order="Ascending"><t:FieldURI FieldURI="` + spec[2] + `"/></t:FieldOrder></m:SortOrder><m:ParentFolderIds>` + ewsFolder(source, folder) + `</m:ParentFolderIds></m:FindItem>`
			page := q.ews(source, request)
			ids := page.first("Items").all("ItemId")
			if len(ids) == 0 {
				break
			}
			for start := 0; start < len(ids); start += 20 {
				end := min(start+20, len(ids))
				var items strings.Builder
				for _, id := range ids[start:end] {
					items.WriteString(`<t:ItemId Id="` + esc(id.Attr["Id"]) + `"/>`)
				}
				response := q.ews(source, `<m:GetItem><m:ItemShape><t:BaseShape>AllProperties</t:BaseShape><t:IncludeMimeContent>true</t:IncludeMimeContent><t:BodyType>Text</t:BodyType></m:ItemShape><m:ItemIds>`+items.String()+`</m:ItemIds></m:GetItem>`)
				for _, container := range response.all("Items") {
					for _, item := range container.Children {
						mime := item.first("MimeContent").Text
						if mime == "" {
							continue
						}
						raw := must(base64.StdEncoding.DecodeString(mime))
						parsed := must(parseMail(raw, item.first("ItemId").Attr["Id"]))
						if id := item.first("ConversationId").Attr["Id"]; id != "" {
							parsed.Event["thread_external_id"] = id
						}
						field := "DateTimeReceived"
						if spec[1] == "OUTGOING" {
							field = "DateTimeSent"
						}
						if occurred := timestamp(item.first(field).Text); occurred != nil {
							parsed.Event["occurred_at"] = *occurred
							if occurred.After(latest) {
								latest = *occurred
							}
						}
						calendar := obj(obj(parsed.Event, "raw_headers"), "Calendar-Event")
						if len(calendar) > 0 {
							location := must(time.LoadLocation(str(obj(settings, "server"), "timezone")))
							for _, key := range []string{"starts_at", "ends_at"} {
								if t := timestamp(calendar[key]); t != nil {
									_, offset := t.Zone()
									if offset == 0 {
										calendar[key] = time.Date(t.Year(), t.Month(), t.Day(), t.Hour(), t.Minute(), t.Second(), 0, location).Format(time.RFC3339)
									}
								}
							}
						}
						if q.persistMail(source, spec[1], parsed) {
							count++
						}
					}
				}
			}
			if page.first("RootFolder").Attr["IncludesLastItemInRange"] == "true" {
				break
			}
		}
		q.setCursor(sourceID, key, latest.Format(time.RFC3339Nano))
	}
	count += q.exchangeCalendar(source)
	return count
}
func (q *request) exchangeCalendar(source M) int {
	sourceID := str(source, "id")
	if last := timestamp(q.cursor(sourceID, "exchange_calendar_sync")); last != nil && time.Since(*last) < 15*time.Minute {
		return 0
	}
	now := time.Now().UTC()
	start, end := now.AddDate(0, 0, -30), now.AddDate(0, 0, 365)
	count := 0
	seen := map[string]bool{}
	mailbox := func(node *xmlNode) M {
		return M{"name": node.first("Name").Text, "address": node.first("EmailAddress").Text}
	}
	for from := start; from.Before(end); {
		to := from.AddDate(0, 0, 31)
		if to.After(end) {
			to = end
		}
		response := q.ews(source, `<m:FindItem Traversal="Shallow"><m:ItemShape><t:BaseShape>AllProperties</t:BaseShape><t:BodyType>Text</t:BodyType></m:ItemShape><m:CalendarView MaxEntriesReturned="500" StartDate="`+from.Format(time.RFC3339)+`" EndDate="`+to.Format(time.RFC3339)+`"/><m:ParentFolderIds>`+ewsFolder(source, "calendar")+`</m:ParentFolderIds></m:FindItem>`)
		if response.first("RootFolder").Attr["IncludesLastItemInRange"] == "false" {
			panic(fmt.Errorf("Exchange calendar window exceeds 500 items; cursor not advanced"))
		}
		for _, item := range response.all("CalendarItem") {
			starts, ends := timestamp(item.first("Start").Text), timestamp(item.first("End").Text)
			if starts == nil || ends == nil {
				continue
			}
			uid := item.first("UID").Text
			if uid == "" {
				uid = item.first("ItemId").Attr["Id"]
			}
			recurring := item.first("IsRecurring").Text == "true" || item.first("CalendarItemType").Text == "Occurrence" || item.first("CalendarItemType").Text == "Exception"
			series := uid
			if recurring {
				original := timestamp(item.first("OriginalStart").Text)
				if original == nil {
					original = starts
				}
				stamp := original.UTC().Format("2006-01-02T15:04:05.999999") + "+00:00"
				digest := fmt.Sprintf("%x", sha256.Sum256([]byte(series+"\x00"+stamp)))[:24]
				suffix := "::" + stamp + "::" + digest
				uid = string([]rune(series)[:min(len([]rune(series)), 512-len([]rune(suffix)))]) + suffix
			}
			external := "calendar:" + fmt.Sprintf("%x", sha256.Sum256([]byte(uid)))
			if seen[external] {
				continue
			}
			seen[external] = true
			organizer := mailbox(item.first("Organizer"))
			attendees := []M{}
			for _, name := range []string{"RequiredAttendees", "OptionalAttendees", "Resources"} {
				for _, attendee := range item.first(name).all("Attendee") {
					attendees = append(attendees, mailbox(attendee))
				}
			}
			self := strings.EqualFold(str(organizer, "address"), str(obj(source, "settings"), "primary_smtp_address"))
			others := false
			for _, a := range attendees {
				if !strings.EqualFold(str(a, "address"), str(organizer, "address")) {
					others = true
				}
			}
			if self && !others {
				continue
			}
			status, method := "CONFIRMED", "REQUEST"
			if item.first("IsCancelled").Text == "true" {
				status, method = "CANCELLED", "CANCEL"
			}
			title := item.first("Subject").Text
			if title == "" {
				title = "Встреча"
			}
			body := item.first("Body").Text
			calendar := M{"uid": uid, "series_uid": series, "title": bounded(title, 500), "starts_at": starts.Format(time.RFC3339), "ends_at": ends.Format(time.RFC3339), "all_day": item.first("IsAllDayEvent").Text == "true", "location": item.first("Location").Text, "organizer": organizer, "attendees": attendees, "status": status, "method": method, "calendar_view": true, "recurring": recurring}
			occurred := now
			if modified := timestamp(item.first("LastModifiedTime").Text); modified != nil {
				occurred = *modified
			}
			participants := append([]M{organizer}, attendees...)
			event := M{"source_id": sourceID, "source_type": "exchange", "external_id": external, "event_type": "meeting_invitation", "direction": "INTERNAL", "thread_external_id": "calendar-series:" + fmt.Sprintf("%x", sha256.Sum256([]byte(series))), "subject": bounded(title, 500), "author": organizer["address"], "participants": participants, "occurred_at": occurred, "body": body, "raw_headers": M{"Calendar-Event": calendar, "Calendar-View": true}, "content_hash": hash([]any{calendar, body})}
			rows := q.rows("SELECT * FROM communication_events WHERE source_id=$1 AND external_id=$2", sourceID, external)
			if len(rows) == 0 {
				q.insert("communication_events", event)
				count++
			} else if rows[0]["content_hash"] != event["content_hash"] {
				delete(event, "source_id")
				event["analysis_state"] = "PENDING"
				q.update("communication_events", rows[0]["id"], event)
				count++
			}
		}
		from = to
	}
	for _, event := range q.rows("SELECT * FROM communication_events WHERE source_id=$1 AND event_type='meeting_invitation' AND external_id LIKE 'calendar:%'", sourceID) {
		if seen[str(event, "external_id")] {
			continue
		}
		headers := obj(event, "raw_headers")
		payload := obj(headers, "Calendar-Event")
		t := timestamp(payload["starts_at"])
		if !boolean(payload, "calendar_view") || t == nil || t.Before(start) || t.After(end) || payload["status"] == "CANCELLED" {
			continue
		}
		payload["status"] = "CANCELLED"
		payload["method"] = "CANCEL"
		headers["Calendar-Event"] = payload
		q.update("communication_events", event["id"], M{"raw_headers": headers, "content_hash": hash(headers), "analysis_state": "PENDING", "analysis_error": nil, "next_analysis_at": nil})
		count++
	}
	q.setCursor(sourceID, "exchange_calendar_sync", now.Format(time.RFC3339))
	return count
}
