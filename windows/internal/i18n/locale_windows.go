//go:build windows

package i18n

import "golang.org/x/sys/windows"

func systemLanguage() string {
	id, _, _ := windows.NewLazySystemDLL("kernel32.dll").NewProc("GetUserDefaultUILanguage").Call()
	switch id & 0x3ff {
	case 0x19:
		return "ru"
	case 0x04:
		return "zh"
	default:
		return "en"
	}
}
