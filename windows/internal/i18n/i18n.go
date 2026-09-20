package i18n

import (
	_ "embed"
	"encoding/json"
	"strings"
)

//go:embed translations.json
var data []byte
var catalog map[string]map[string]string
var language = "en"

func init() { _ = json.Unmarshal(data, &catalog); Set(systemLanguage()) }
func Set(value string) {
	switch strings.ToLower(strings.Split(strings.ReplaceAll(value, "_", "-"), "-")[0]) {
	case "ru", "russian":
		language = "ru"
	case "zh", "chinese":
		language = "zh"
	default:
		language = "en"
	}
}
func Tr(source string) string {
	if language == "ru" {
		return source
	}
	if translated := catalog[source][language]; translated != "" {
		return translated
	}
	return source
}
