package mobileqr

import "testing"

func TestBase45Vectors(t *testing.T) {
	for input, want := range map[string]string{"AB": "BB8", "\xff": "U5", "\x00\x00": "000"} {
		if got := base45([]byte(input)); got != want {
			t.Fatalf("base45: %q != %q", got, want)
		}
	}
}
