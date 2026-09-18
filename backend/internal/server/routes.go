package server

func (s *Server) routes() {
	s.taskRoutes()
	s.adminRoutes()
	s.archiveRoutes()
	s.externalRoutes()
	s.llmRoutes()
	s.systemRoutes()
	s.mtsAuthRoutes()
	s.connectorRoutes()
}
