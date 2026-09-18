package server

import (
	"context"
	"mime"
	"testing"

	"golang.org/x/text/encoding/charmap"
)

const encodedSender = "=?UTF-8?B?0JPQvtGB0YPRgdC70YPQs9C4?= <no-reply@gosuslugi.ru>"
const decodedSender = "Госуслуги <no-reply@gosuslugi.ru>"

func TestDecodeMailHeader(t *testing.T) {
	windows1251, err := charmap.Windows1251.NewEncoder().String("Госуслуги")
	if err != nil {
		t.Fatal(err)
	}
	koi8, err := charmap.KOI8R.NewEncoder().String("Почта")
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct{ name, input, want string }{
		{"utf8-base64", encodedSender, decodedSender},
		{"utf8-quoted-printable", mime.QEncoding.Encode("utf-8", "Госуслуги") + " <no-reply@gosuslugi.ru>", decodedSender},
		{"folded-words", mime.BEncoding.Encode("utf-8", "Гос") + "\r\n \t" + mime.BEncoding.Encode("utf-8", "услуги") + " <no-reply@gosuslugi.ru>", decodedSender},
		{"windows1251", mime.BEncoding.Encode("windows-1251", windows1251) + " <no-reply@gosuslugi.ru>", decodedSender},
		{"koi8r", mime.QEncoding.Encode("koi8-r", koi8) + " <sender@example.test>", "Почта <sender@example.test>"},
		{"unicode", decodedSender, decodedSender},
		{"plain", "Sender <sender@example.test>", "Sender <sender@example.test>"},
		{"empty", "", ""},
		{"malformed", "=?UTF-8?B?!!!?= <sender@example.test>", "=?UTF-8?B?!!!?= <sender@example.test>"},
		{"unknown-charset", "=?x-unknown?B?VGVzdA==?= <sender@example.test>", "=?x-unknown?B?VGVzdA==?= <sender@example.test>"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			if got := decodeMailHeader(test.input); got != test.want {
				t.Fatalf("got %q, want %q", got, test.want)
			}
		})
	}
}

func TestParseMailDecodesSender(t *testing.T) {
	parsed, err := parseMail([]byte("From: "+encodedSender+"\r\nTo: user@example.test\r\nMessage-ID: <encoded>\r\nSubject: Test\r\nDate: Thu, 17 Sep 2026 10:00:00 +0300\r\n\r\nTest body"), "1")
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Event["author"] != decodedSender {
		t.Fatal(parsed.Event["author"])
	}
	people := parsed.Event["participants"].([]M)
	if len(people) != 2 || people[0]["name"] != "Госуслуги" || people[0]["address"] != "no-reply@gosuslugi.ru" {
		t.Fatal(people)
	}
}

func TestLegacyMailSenderDecodedInAPI(t *testing.T) {
	s := testServer(t)
	call(t, s, "POST", "/api/v1/admin/sources", M{"id": "mail", "label": "Mail", "source_type": "imap", "enabled": false}, 201)
	var event M
	_, err := s.job(context.Background(), func(q *request) bool {
		old := threadTestMail(t, "legacy-encoded", "").Event
		old["source_id"] = "mail"
		old["source_type"] = "imap"
		old["direction"] = "INCOMING"
		old["content_hash"] = "existing-hash"
		old["author"] = encodedSender
		event = q.insert("communication_events", old)
		q.rebuildThread(event, M{})
		return true
	})
	if err != nil {
		t.Fatal(err)
	}
	original := call(t, s, "GET", "/api/v1/events/"+str(event, "id"), nil, 200).(map[string]any)
	if original["author"] != decodedSender {
		t.Fatal(original["author"])
	}
	threads := call(t, s, "GET", "/api/v1/threads", nil, 200).(map[string]any)["items"].([]any)
	detail := call(t, s, "GET", "/api/v1/threads/"+str(threads[0].(map[string]any), "id"), nil, 200).(map[string]any)
	if got := detail["events"].([]any)[0].(map[string]any)["author"]; got != decodedSender {
		t.Fatal(got)
	}
	var stored, hash, state string
	if err = s.Pool.QueryRow(context.Background(), "SELECT author,content_hash,analysis_state FROM communication_events WHERE id=$1", event["id"]).Scan(&stored, &hash, &state); err != nil {
		t.Fatal(err)
	}
	if stored != encodedSender || hash != "existing-hash" || state != "PENDING" {
		t.Fatal("reading altered the archived message")
	}
}
