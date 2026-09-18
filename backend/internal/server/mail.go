package server

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"fmt"
	"io"
	"mime"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/emersion/go-imap"
	imapclient "github.com/emersion/go-imap/client"
	"github.com/emersion/go-message"
	"github.com/emersion/go-message/charset"
	messageMail "github.com/emersion/go-message/mail"
	"golang.org/x/net/html"
)

type mailAttachment struct {
	Filename, MediaType string
	Data                []byte
}
type parsedMail struct {
	Event       M
	Attachments []mailAttachment
}

func htmlText(value string) string {
	root, e := html.Parse(strings.NewReader(value))
	if e != nil {
		return value
	}
	var b strings.Builder
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.ElementNode && (n.Data == "script" || n.Data == "style") {
			return
		}
		if n.Type == html.TextNode {
			b.WriteString(n.Data)
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			visit(c)
		}
		if n.Type == html.ElementNode && (n.Data == "p" || n.Data == "div" || n.Data == "br" || n.Data == "tr") {
			b.WriteByte('\n')
		}
	}
	visit(root)
	return strings.TrimSpace(b.String())
}

// decodeMailHeader handles RFC 2047 encoded words, including legacy mail
// charsets. Keep malformed or unknown encodings intact instead of losing data.
func decodeMailHeader(value string) string {
	if !strings.Contains(value, "=?") {
		return value
	}
	decoder := mime.WordDecoder{CharsetReader: charset.Reader}
	decoded, err := decoder.DecodeHeader(value)
	if err != nil {
		return value
	}
	return decoded
}

func parseMail(raw []byte, uid string) (parsedMail, error) {
	reader, e := messageMail.CreateReader(bytes.NewReader(raw))
	if e != nil && !message.IsUnknownCharset(e) {
		return parsedMail{}, e
	}
	defer reader.Close()
	subject, _ := reader.Header.Subject()
	occurred, e := reader.Header.Date()
	if e != nil {
		occurred = time.Now().UTC()
	}
	headers := M{}
	for _, key := range []string{"Auto-Submitted", "Message-ID", "In-Reply-To", "Importance", "References", "Thread-Index", "Priority", "X-Priority", "X-MSMail-Priority", "Precedence", "X-Autoreply", "X-Autorespond", "List-Id", "List-Unsubscribe"} {
		headers[key] = reader.Header.Get(key)
	}
	external := str(headers, "Message-ID")
	if external == "" {
		external = uid
	}
	thread := external
	if refs := strings.Fields(str(headers, "References")); len(refs) > 0 {
		thread = refs[0]
	} else if reply := str(headers, "In-Reply-To"); reply != "" {
		thread = reply
	}
	if index, e := base64.StdEncoding.DecodeString(str(headers, "Thread-Index")); e == nil && len(index) >= 22 {
		thread = "thread-index:" + base64.StdEncoding.EncodeToString(index[:22])
	}
	participants := []M{}
	for _, key := range []string{"From", "To", "Cc"} {
		addresses, _ := reader.Header.AddressList(key)
		for _, a := range addresses {
			participants = append(participants, M{"name": a.Name, "address": a.Address, "role": strings.ToLower(key)})
		}
	}
	plain, rich := []string{}, []string{}
	attachments := []mailAttachment{}
	eventType := "email"
	for {
		part, e := reader.NextPart()
		if e == io.EOF {
			break
		}
		if e != nil && !message.IsUnknownCharset(e) {
			return parsedMail{}, e
		}
		if part == nil {
			continue
		}
		data, e := io.ReadAll(io.LimitReader(part.Body, 26214401))
		if e != nil {
			return parsedMail{}, e
		}
		var media string
		var params map[string]string
		switch h := part.Header.(type) {
		case *messageMail.InlineHeader:
			media, params, _ = h.ContentType()
			if media == "text/plain" {
				plain = append(plain, string(data))
			} else if media == "text/html" {
				rich = append(rich, htmlText(string(data)))
			}
		case *messageMail.AttachmentHeader:
			media, params, _ = h.ContentType()
			filename, _ := h.Filename()
			filename = filepath.Base(strings.ReplaceAll(filename, "\\", "/"))
			if filename == "" || filename == "." {
				filename = "attachment"
			}
			if len(data) <= 26214400 {
				attachments = append(attachments, mailAttachment{bounded(filename, 240), media, data})
			}
		}
		if media == "text/calendar" {
			headers["Calendar-Method"] = params["method"]
			if calendar := parseCalendar(string(data), params["method"]); calendar != nil {
				headers["Calendar-Event"] = calendar
				eventType = "meeting_invitation"
			}
		}
	}
	body := strings.Join(plain, "\n")
	if body == "" {
		body = strings.Join(rich, "\n")
	}
	event := M{"external_id": external, "thread_external_id": thread, "subject": subject, "author": decodeMailHeader(reader.Header.Get("From")), "participants": participants, "occurred_at": occurred.UTC(), "body": strings.TrimSpace(body), "raw_headers": headers, "event_type": eventType}
	return parsedMail{event, attachments}, nil
}
func parseCalendar(text, method string) M {
	type property struct {
		name, value string
		params      map[string]string
	}
	lines := []string{}
	for _, line := range strings.Split(strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n"), "\n") {
		if (strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t")) && len(lines) > 0 {
			lines[len(lines)-1] += line[1:]
		} else {
			lines = append(lines, line)
		}
	}
	properties := map[string][]property{}
	inside := false
	for _, line := range lines {
		quote := false
		split := -1
		for i, c := range line {
			if c == '"' {
				quote = !quote
			}
			if c == ':' && !quote {
				split = i
				break
			}
		}
		if split < 0 {
			continue
		}
		parts := strings.Split(line[:split], ";")
		name, value := strings.ToUpper(parts[0]), line[split+1:]
		params := map[string]string{}
		for _, p := range parts[1:] {
			kv := strings.SplitN(p, "=", 2)
			if len(kv) == 2 {
				params[strings.ToUpper(kv[0])] = strings.Trim(kv[1], "\"")
			}
		}
		if name == "METHOD" && !inside {
			method = value
		}
		if name == "BEGIN" && value == "VEVENT" {
			inside = true
		} else if name == "END" && value == "VEVENT" {
			break
		} else if inside {
			properties[name] = append(properties[name], property{name, value, params})
		}
	}
	first := func(k string) property {
		if len(properties[k]) > 0 {
			return properties[k][0]
		}
		return property{params: map[string]string{}}
	}
	if strings.EqualFold(method, "REPLY") || first("UID").value == "" || first("DTSTART").value == "" {
		return nil
	}
	parseDate := func(p property) (time.Time, bool, error) {
		if len(p.value) == 8 || p.params["VALUE"] == "DATE" {
			t, e := time.Parse("20060102", p.value)
			return t, true, e
		}
		if strings.HasSuffix(p.value, "Z") {
			t, e := time.Parse("20060102T150405Z", p.value)
			return t, false, e
		}
		loc := time.UTC
		if name := p.params["TZID"]; name != "" {
			if l, e := time.LoadLocation(name); e == nil {
				loc = l
			} else if match := regexp.MustCompile(`UTC\s*([+-])(\d{1,2}):(\d{2})`).FindStringSubmatch(name); match != nil {
				h, _ := strconv.Atoi(match[2])
				m, _ := strconv.Atoi(match[3])
				offset := (h*60 + m) * 60
				if match[1] == "-" {
					offset = -offset
				}
				loc = time.FixedZone(name, offset)
			}
		}
		t, e := time.ParseInLocation("20060102T150405", p.value, loc)
		return t, false, e
	}
	start, allDay, e := parseDate(first("DTSTART"))
	if e != nil {
		return nil
	}
	end := start.Add(time.Hour)
	if allDay {
		end = start.AddDate(0, 0, 1)
	}
	if first("DTEND").value != "" {
		end, _, e = parseDate(first("DTEND"))
		if e != nil {
			return nil
		}
	}
	unescape := func(s string) string {
		return strings.TrimSpace(strings.NewReplacer(`\n`, "\n", `\N`, "\n", `\,`, ",", `\;`, ";", `\\`, `\`).Replace(s))
	}
	mailbox := func(p property) M {
		return M{"name": unescape(p.params["CN"]), "address": strings.TrimPrefix(strings.TrimPrefix(p.value, "mailto:"), "MAILTO:")}
	}
	var organizer any
	if first("ORGANIZER").value != "" {
		organizer = mailbox(first("ORGANIZER"))
	}
	attendees := []M{}
	for _, p := range properties["ATTENDEE"] {
		if len(attendees) == 200 {
			break
		}
		attendees = append(attendees, mailbox(p))
	}
	status := strings.ToUpper(first("STATUS").value)
	method = strings.ToUpper(method)
	if method == "" {
		method = "REQUEST"
	}
	if status == "" {
		status = "CONFIRMED"
		if method == "CANCEL" {
			status = "CANCELLED"
		}
	}
	title := unescape(first("SUMMARY").value)
	if title == "" {
		title = "Встреча"
	}
	return M{"uid": bounded(first("UID").value, 512), "title": bounded(title, 500), "starts_at": start.Format(time.RFC3339), "ends_at": end.Format(time.RFC3339), "all_day": allDay, "location": unescape(first("LOCATION").value), "organizer": organizer, "attendees": attendees, "status": status, "method": method}
}
func (q *request) cursor(source, key string) string {
	rows := q.rows("SELECT cursor_value FROM source_cursors WHERE source_id=$1 AND cursor_key=$2", source, key)
	if len(rows) > 0 {
		return str(rows[0], "cursor_value")
	}
	return ""
}
func (q *request) setCursor(source, key, value string) {
	q.exec("INSERT INTO source_cursors (id,source_id,cursor_key,cursor_value) VALUES ($1,$2,$3,$4) ON CONFLICT(source_id,cursor_key) DO UPDATE SET cursor_value=EXCLUDED.cursor_value,updated_at=now()", newID(), source, key, value)
}

var replyPrefix = regexp.MustCompile(`(?i)^(?:(?:re|fw|fwd|aw|wg|ответ|пересылка|пересл|переадресовано)\s*(?:\[\d+\]|\(\d+\))?\s*:\s*)+`)

func (q *request) persistMail(source M, direction string, parsed parsedMail) bool {
	event := parsed.Event
	event["source_id"] = source["id"]
	event["source_type"] = source["source_type"]
	event["direction"] = direction
	event["content_hash"] = fmt.Sprintf("%x", sha256.Sum256([]byte(strings.Join([]string{str(event, "author"), pythonTimestamp(event["occurred_at"]), str(event, "subject"), str(event, "body")}, "\x00"))))
	rows := q.rows("SELECT * FROM communication_events WHERE source_id=$1 AND external_id=$2", source["id"], event["external_id"])
	if len(rows) > 0 {
		old := rows[0]
		changed := false
		for _, k := range []string{"Calendar-Event", "Importance", "Priority", "X-Priority", "X-MSMail-Priority"} {
			if hash(obj(old, "raw_headers")[k]) != hash(obj(event, "raw_headers")[k]) {
				changed = true
			}
		}
		if changed {
			q.update("communication_events", old["id"], M{"event_type": event["event_type"], "raw_headers": event["raw_headers"], "analysis_state": "PENDING", "analysis_error": nil})
		}
		return changed
	}
	if event["event_type"] == "email" {
		title := strings.ToLower(replyPrefix.ReplaceAllString(clean(str(event, "subject")), ""))
		if title != "" {
			event["subject_key"] = "subject:" + fmt.Sprintf("%x", sha256.Sum256([]byte(title)))
		}
		event["subject_tokens"] = regexp.MustCompile(`[\pL\pN_-]+`).FindAllString(title, -1)
		if event["subject_tokens"] == nil {
			event["subject_tokens"] = []string{}
		}
	}
	row := q.insert("communication_events", event)
	for _, attachment := range parsed.Attachments {
		if len(attachment.Data) > int(num(obj(q.settings(), "document_parser"), "max_bytes")) {
			continue
		}
		digest := fmt.Sprintf("%x", sha256.Sum256(attachment.Data))
		suffix := bounded(strings.ToLower(filepath.Ext(attachment.Filename)), 16)
		dir := filepath.Join(q.server.Config.DataDir, "attachments")
		check(os.MkdirAll(dir, 0700))
		path := filepath.Join(dir, digest+suffix)
		check(os.WriteFile(path, attachment.Data, 0600))
		q.insert("attachments", M{"event_id": row["id"], "filename": attachment.Filename, "media_type": attachment.MediaType, "size_bytes": len(attachment.Data), "sha256": digest, "storage_path": path})
	}
	if row["event_type"] == "email" {
		q.rebuildThread(row, M{})
	}
	return true
}
func (q *request) imapSync(source M, testOnly bool) int {
	settings := obj(source, "settings")
	credential, _ := q.sourceTokens(source)
	host := str(settings, "host")
	port := int(num(settings, "port"))
	address := net.JoinHostPort(host, strconv.Itoa(port))
	raw := must((&net.Dialer{Timeout: 30 * time.Second}).DialContext(q.Context, "tcp", address))
	defer raw.Close()
	stop := context.AfterFunc(q.Context, func() { raw.Close() })
	defer stop()
	tlsConfig := &tls.Config{ServerName: host, MinVersion: tls.VersionTLS12}
	var client *imapclient.Client
	if tlsValue, ok := settings["tls"]; !ok || tlsValue == true {
		conn := tls.Client(raw, tlsConfig)
		check(conn.HandshakeContext(q.Context))
		client = must(imapclient.New(conn))
	} else {
		client = must(imapclient.New(raw))
		client.Timeout = 45 * time.Second
		check(client.StartTLS(tlsConfig))
	}
	client.Timeout = 45 * time.Second
	defer client.Logout()
	check(client.Login(str(settings, "username"), credential))
	inserted := 0
	for _, spec := range [][3]string{{"inbox_folder", "INBOX", "INCOMING"}, {"sent_folder", "Sent", "OUTGOING"}} {
		folder := str(settings, spec[0])
		if folder == "" {
			folder = spec[1]
		}
		mailbox := must(client.Select(folder, true))
		if testOnly {
			continue
		}
		key := "imap_uid:" + folder
		last, _ := strconv.ParseUint(q.cursor(str(source, "id"), key), 10, 32)
		validity := strconv.FormatUint(uint64(mailbox.UidValidity), 10)
		validityKey := "imap_uidvalidity:" + folder
		if previous := q.cursor(str(source, "id"), validityKey); previous != "" && previous != validity {
			last = 0
		}
		criteria := imap.NewSearchCriteria()
		if last > 0 {
			criteria.Uid = new(imap.SeqSet)
			criteria.Uid.AddRange(uint32(last)+1, 0)
		} else {
			criteria.Since = time.Now().AddDate(0, 0, -int(num(obj(q.settings(), "communication_sources"), "initial_sync_days")))
		}
		uids := must(client.UidSearch(criteria))
		for _, uid := range uids {
			if uint64(uid) <= last {
				continue
			}
			set := new(imap.SeqSet)
			set.AddNum(uid)
			section := &imap.BodySectionName{Peek: true}
			messages := make(chan *imap.Message, 1)
			done := make(chan error, 1)
			go func() { done <- client.UidFetch(set, []imap.FetchItem{imap.FetchUid, section.FetchItem()}, messages) }()
			for msg := range messages {
				body := msg.GetBody(section)
				if body == nil {
					continue
				}
				data := must(io.ReadAll(io.LimitReader(body, 64<<20)))
				parsed := must(parseMail(data, fmt.Sprint(uid)))
				if q.persistMail(source, spec[2], parsed) {
					inserted++
				}
			}
			check(<-done)
			last = uint64(uid)
		}
		q.setCursor(str(source, "id"), key, strconv.FormatUint(last, 10))
		q.setCursor(str(source, "id"), validityKey, validity)
	}
	return inserted
}

func pythonTimestamp(value any) string {
	t := timestamp(value)
	if t == nil {
		return ""
	}
	layout := "2006-01-02T15:04:05"
	if t.Nanosecond() != 0 {
		layout += ".000000"
	}
	return t.Format(layout + "-07:00")
}
