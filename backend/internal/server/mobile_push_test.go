package server

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestMobilePushAddsAPNsWithoutChangingAndroidData(t *testing.T) {
	data := M{"type": "TASK_REMINDER", "object_id": "test-task", "task_title": "Private subject"}
	message := mobilePushMessage("test-token", data)["message"].(M)
	if message["android"].(M)["priority"] != "high" || message["data"].(M)["object_id"] != "test-task" {
		t.Fatal("Android delivery changed")
	}
	apns := message["apns"].(M)
	if apns["headers"].(M)["apns-push-type"] != "alert" || apns["payload"].(M)["aps"].(M)["content-available"] != 1 {
		t.Fatal("missing Apple delivery options")
	}
	encoded, _ := json.Marshal(apns)
	if strings.Contains(string(encoded), "Private subject") {
		t.Fatal("private content in lock-screen alert")
	}
}

func TestMobilePushLanguage(t *testing.T) {
	for language, title := range map[string]string{"ru": "AI Секретарь", "en": "AI Secretary", "zh": "AI 秘书"} {
		data := M{"task_title": "Настройки", "type": "NEW_TASK"}
		message := mobilePushMessage("token", data, language)["message"].(M)
		alert := message["apns"].(M)["payload"].(M)["aps"].(M)["alert"].(M)
		if alert["title"] != title || message["data"].(M)["task_title"] != "Настройки" {
			t.Fatal(language, message)
		}
	}
}

func TestDeviceLanguageIsValidatedAndUpdated(t *testing.T) {
	s := testServer(t)
	token := "language-test-device-token"
	for _, language := range []string{"ru", "zh", "en"} {
		call(t, s, "PUT", "/api/v1/devices/current", M{"label": "iOS", "fcm_token": token, "language": language}, 200)
		var saved string
		if err := s.Pool.QueryRow(context.Background(), "SELECT language FROM devices WHERE fcm_token=$1", token).Scan(&saved); err != nil || saved != language {
			t.Fatal(saved, err)
		}
	}
	call(t, s, "PUT", "/api/v1/devices/current", M{"label": "iOS", "fcm_token": token, "language": "invalid"}, 422)
}
