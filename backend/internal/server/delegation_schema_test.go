package server

import (
	"bytes"
	"encoding/json"
	"testing"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

func TestDelegationSchemaOnlyAllowsKnownReferences(t *testing.T) {
	for _, name := range []string{"DelegationAnalysis", "MeetingDelegationAnalysis"} {
		for _, existing := range [][]M{nil, {{"id": "existing-id"}}} {
			input := M{"existing_delegations": existing}
			generated := generationSchema(name, input)
			var schema M
			if err := json.Unmarshal(generated, &schema); err != nil {
				t.Fatal(err)
			}
			property := obj(obj(obj(obj(schema, "properties"), "items"), "items"), "properties")["delegation_id"]
			c := jsonschema.NewCompiler()
			if err := c.AddResource("https://test.invalid/id", property); err != nil {
				t.Fatal(err)
			}
			compiled, err := c.Compile("https://test.invalid/id")
			if err != nil {
				t.Fatal(err)
			}
			if err = compiled.Validate(""); err != nil {
				t.Fatal(err)
			}
			if err = compiled.Validate("1"); err == nil {
				t.Fatal("invented ID accepted")
			}
			if (compiled.Validate("existing-id") == nil) != (len(existing) > 0) {
				t.Fatal("existing ID scope mismatch")
			}
		}
	}
	if !bytes.Equal(generationSchema("AnalysisResult", M{}), llmWireSchemas["AnalysisResult"]) {
		t.Fatal("unrelated schema changed")
	}
}
