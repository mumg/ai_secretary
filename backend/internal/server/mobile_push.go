package server

// APNs options are ignored for Android tokens. Keep the lock-screen alert
// generic; the app opens the corresponding object over its authenticated API.
func mobilePushMessage(token any, data M, languages ...string) M {
	title, body := "AI Секретарь", "Есть обновления. Откройте приложение, чтобы посмотреть."
	if len(languages) > 0 {
		switch languages[0] {
		case "zh":
			title, body = "AI 秘书", "有新更新。打开应用查看。"
		case "en":
			title, body = "AI Secretary", "Updates are available. Open the app to view them."
		}
	}
	return M{"message": M{
		"token": token, "data": data, "android": M{"priority": "high"},
		"apns": M{
			"headers": M{"apns-push-type": "alert", "apns-priority": "10"},
			"payload": M{"aps": M{
				"alert": M{"title": title, "body": body},
				"sound": "default", "content-available": 1,
			}},
		},
	}}
}
