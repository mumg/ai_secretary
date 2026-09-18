//go:build windows

package setup

import (
	"golang.org/x/sys/windows"
	"reflect"
	"testing"
)

func TestCommandLineWindowsRoundTrip(t *testing.T) {
	args := []string{`C:\Program Files\AI Secretary\improver.exe`, "", `C:\Data & More\`, "quote\"inside", `slash\"quote`, "Русский текст"}
	got, err := windows.DecomposeCommandLine(CommandLine(args...))
	if err != nil || !reflect.DeepEqual(got, args) {
		t.Fatal(got, err)
	}
}
