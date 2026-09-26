package server

func (s *Server) routes() {
	s.taskRoutes()
	s.delegationRoutes()
	s.adminRoutes()
	s.archiveRoutes()
	s.externalRoutes()
	s.llmRoutes()
	s.systemRoutes()
	s.mtsAuthRoutes()
	s.connectorRoutes()
	s.diagnosticRoutes()
	s.diagnosticDeliveryRoutes()
	s.temporaryCorrectionRoutes()
}
