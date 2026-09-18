package server

// sourceFailure is safe to show to users: only application-authored messages,
// never upstream response bodies, URLs or credentials.
type sourceFailure struct {
	message    string
	httpStatus int
}

func (e *sourceFailure) Error() string { return e.message }

func (s *Server) connectorRoutes() {
	s.route("POST /api/v1/admin/sources/{source}/test", true, func(q *request) (result any) {
		source := q.get("communication_sources", q.r.PathValue("source"))
		defer func() {
			if err := recover(); err != nil {
				message := "Ошибка подключения; проверьте адрес и учётные данные"
				if safe, ok := err.(*sourceFailure); ok {
					message = safe.Error()
				}
				q.update("communication_sources", source["id"], M{"last_error": message})
				q.status = 502
				result = M{"detail": message}
			}
		}()
		switch source["source_type"] {
		case "imap":
			q.imapSync(source, true)
		case "exchange":
			q.exchangeSync(source, true)
		case "mts_link":
			q.mtsSync(source, true)
		case "external_tasks":
		default:
			fail(422, "Unknown source type")
		}
		q.update("communication_sources", source["id"], M{"last_error": nil})
		return M{"status": "ok", "detail": "Connection successful"}
	})
}
