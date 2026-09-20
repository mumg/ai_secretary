//go:build !windows

package i18n

import "os"

func systemLanguage() string { return os.Getenv("LANG") }
