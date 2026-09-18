package contracts

import (
	_ "embed"
	"encoding/json"
	"fmt"
	"regexp"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

//go:embed openapi.json
var OpenAPI []byte
var requests map[string]*jsonschema.Schema
var responses map[string]map[string]*jsonschema.Schema
var pathParameter = regexp.MustCompile(`\{[^}]+\}`)

func PathKey(pattern string) string { return pathParameter.ReplaceAllString(pattern, "{}") }
func init() {
	var doc map[string]any
	if err := json.Unmarshal(OpenAPI, &doc); err != nil {
		panic(err)
	}
	compiler := jsonschema.NewCompiler()
	if err := compiler.AddResource("https://improver.invalid/openapi.json", doc); err != nil {
		panic(err)
	}
	requests = map[string]*jsonschema.Schema{}
	responses = map[string]map[string]*jsonschema.Schema{}
	for path, operations := range doc["paths"].(map[string]any) {
		for method, operation := range operations.(map[string]any) {
			op, ok := operation.(map[string]any)
			if !ok {
				continue
			}
			key := PathKey(strings.ToUpper(method) + " " + path)
			escaped := strings.NewReplacer("~", "~0", "/", "~1").Replace(path)
			if definitions, ok := op["responses"].(map[string]any); ok {
				responses[key] = map[string]*jsonschema.Schema{}
				for status, definition := range definitions {
					response, _ := definition.(map[string]any)
					content, _ := response["content"].(map[string]any)
					if content["application/json"] != nil {
						schema, err := compiler.Compile("https://improver.invalid/openapi.json#/paths/" + escaped + "/" + method + "/responses/" + status + "/content/application~1json/schema")
						if err != nil {
							panic(err)
						}
						responses[key][status] = schema
					}
				}
			}
			body, ok := op["requestBody"].(map[string]any)
			if !ok {
				continue
			}
			content := body["content"].(map[string]any)
			if content["application/json"] == nil {
				continue
			}
			escaped = strings.NewReplacer("~", "~0", "/", "~1").Replace(path)
			schema, err := compiler.Compile("https://improver.invalid/openapi.json#/paths/" + escaped + "/" + method + "/requestBody/content/application~1json/schema")
			if err != nil {
				panic(err)
			}
			requests[PathKey(strings.ToUpper(method)+" "+path)] = schema
		}
	}
}
func ValidateRequest(pattern string, value map[string]any) error {
	if schema := requests[PathKey(pattern)]; schema != nil {
		if err := schema.Validate(value); err != nil {
			return fmt.Errorf("request does not match the API schema: %w", err)
		}
	}
	return nil
}

func ValidateResponse(pattern, status string, value any) error {
	if schema := responses[PathKey(pattern)][status]; schema != nil {
		return schema.Validate(value)
	}
	return nil
}
