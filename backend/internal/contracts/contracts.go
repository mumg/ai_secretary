// Package contracts contains the public JSON projections of the v1 API.
package contracts

import (
	_ "embed"
	"encoding/json"
)

//go:embed fields.json
var definitions []byte
var Fields map[string][]string

func init() {
	if err := json.Unmarshal(definitions, &Fields); err != nil {
		panic(err)
	}
}
func Project(name string, value map[string]any) map[string]any {
	result := map[string]any{}
	for _, k := range Fields[name] {
		result[k] = value[k]
	}
	return result
}
