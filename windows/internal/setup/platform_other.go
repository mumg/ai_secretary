//go:build !windows

package setup

import "errors"
import "github.com/mumg/ai_secretary/windows/internal/i18n"

func RequireAdministrator() error {
	return errors.New(i18n.Tr("установочный помощник запускается только на Windows"))
}
func serviceState(string) (string, error) { return "", RequireAdministrator() }
func currentSID() (string, error)         { return "", RequireAdministrator() }
