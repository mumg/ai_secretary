//go:build windows

package setup

import (
	"errors"
	"github.com/mumg/ai_secretary/windows/internal/i18n"
	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

func RequireAdministrator() error {
	if !windows.GetCurrentProcessToken().IsElevated() {
		return errors.New(i18n.Tr("запустите установку с правами администратора Windows"))
	}
	return nil
}
func serviceState(name string) (string, error) {
	m, err := mgr.Connect()
	if err != nil {
		return "", err
	}
	defer m.Disconnect()
	s, err := m.OpenService(name)
	if errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST) {
		return "missing", nil
	}
	if err != nil {
		return "", err
	}
	defer s.Close()
	status, err := s.Query()
	if err != nil {
		return "", err
	}
	if status.State == svc.Stopped {
		return "stopped", nil
	}
	return "running", nil
}
func currentSID() (string, error) {
	t, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return "", err
	}
	return t.User.Sid.String(), nil
}
