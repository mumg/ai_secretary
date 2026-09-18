//go:build !windows

package setup

import "errors"

func RequireAdministrator() error {
	return errors.New("установочный помощник запускается только на Windows")
}
func serviceState(string) (string, error) { return "", RequireAdministrator() }
func currentSID() (string, error)         { return "", RequireAdministrator() }
