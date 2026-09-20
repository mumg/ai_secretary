package server

// APNs options are ignored for Android tokens. Keep the lock-screen alert
// generic; the app opens the corresponding object over its authenticated API.
func mobilePushMessage(token any, data M) M {
	return M{"message": M{
		"token": token, "data": data, "android": M{"priority": "high"},
		"apns": M{
			"headers": M{"apns-push-type": "alert", "apns-priority": "10"},
			"payload": M{"aps": M{
				"alert": M{"title": "AI Секретарь", "body": "Есть обновления. Откройте приложение, чтобы посмотреть."},
				"sound": "default", "content-available": 1,
			}},
		},
	}}
}
