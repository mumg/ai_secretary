package server

import (
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
