package setup

import (
	"archive/zip"
	"bytes"
	"crypto/x509"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"software.sslmate.com/src/go-pkcs12"
)

func TestOptionsAndServiceConfiguration(t *testing.T) {
	o, err := DefaultOptions().Validate()
	if err != nil {
		t.Fatal(err)
	}
	root, data := filepath.Join(t.TempDir(), "Программа & Files"), filepath.Join(t.TempDir(), "Data & More")
	env := Environment(data, o)
	if env["LOCAL_WEB_ONLY"] != "true" || env["PUBLIC_URL"] != "http://127.0.0.1:18000" || env["DOCUMENT_PARSER_URL"] != "http://127.0.0.1:18080" {
		t.Fatal(env)
	}
	for _, name := range Services {
		doc, err := ServiceXML(root, data, o, name)
		if err != nil {
			t.Fatal(err)
		}
		var fields struct {
			StartMode  string   `xml:"startmode"`
			User       string   `xml:"serviceaccount>user"`
			Executable string   `xml:"executable"`
			Depends    []string `xml:"depend"`
			Arguments  string   `xml:"arguments"`
		}
		if err := xml.Unmarshal([]byte(doc), &fields); err != nil {
			t.Fatal(err)
		}
		if fields.StartMode != "Automatic" || fields.User != "LocalService" || strings.Contains(doc, "0.0.0.0") || strings.Contains(doc, "python") {
			t.Fatal(doc)
		}
		if !strings.Contains(doc, "&amp;") {
			t.Fatal("XML did not escape data path")
		}
		if name == "AISecretaryApi" && (!strings.HasSuffix(fields.Executable, "improver.exe") || !reflect.DeepEqual(fields.Depends, []string{"AISecretaryDatabase", "AISecretaryParser"})) {
			t.Fatal(fields)
		}
		if name == "AISecretaryParser" && fields.Arguments != "serve --listen 127.0.0.1:18080" {
			t.Fatal(fields)
		}
	}
	for _, change := range []func(*Options){func(o *Options) { o.APIPort = 80 }, func(o *Options) { o.APIPort = o.DatabasePort }, func(o *Options) { o.PublicHost = "example.org { respond 200 }" }, func(o *Options) { o.PublicHost = "https://example.org" }, func(o *Options) { o.PublicHost = "example.local" }, func(o *Options) { o.LLMURL = "http://user:password@example.org" }, func(o *Options) { o.LLMURL = "file:///secret" }, func(o *Options) { o.LLMURL = "http://example.org?x=1" }} {
		o := DefaultOptions()
		change(&o)
		if _, err := o.Validate(); err == nil {
			t.Fatal("invalid options accepted", o)
		}
	}
	o.PublicHost = "assistant.example.org"
	o, err = o.Validate()
	if err != nil {
		t.Fatal(err)
	}
	config := CaddyConfig(data, o)
	for _, needle := range []string{"disable_tlsalpn_challenge", "require_and_verify", "reverse_proxy 127.0.0.1:18000"} {
		if !strings.Contains(config, needle) {
			t.Fatal(config)
		}
	}
	if Environment(data, o)["LOCAL_WEB_ONLY"] != "false" {
		t.Fatal("public host did not enable HTTPS")
	}
}
func TestCommandLineQuoting(t *testing.T) {
	for _, tc := range []struct{ arg, want string }{{"plain", "plain"}, {"", `""`}, {`C:\Program Files\`, `"C:\Program Files\\"`}, {`x"y`, `"x\"y"`}, {`a\"b`, `"a\\\"b"`}} {
		if got := CommandLine(tc.arg); got != tc.want {
			t.Errorf("%q => %q, want %q", tc.arg, got, tc.want)
		}
	}
}
func TestCertificatesProtectedAndPreserved(t *testing.T) {
	data := t.TempDir()
	if err := CreateCertificates(data); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(data, "certificates")
	blob := must(os.ReadFile(filepath.Join(dir, "client.p12")))
	password := must(readText(filepath.Join(dir, "client-password.txt")))
	key, cert, chain, err := pkcs12.DecodeChain(blob, password)
	if err != nil || key == nil || len(chain) != 1 {
		t.Fatal(err)
	}
	if len(cert.ExtKeyUsage) != 1 || cert.ExtKeyUsage[0] != x509.ExtKeyUsageClientAuth || cert.CheckSignatureFrom(chain[0]) != nil || cert.NotAfter.Sub(cert.NotBefore) < 729*24*time.Hour {
		t.Fatal(cert)
	}
	if _, _, _, err = pkcs12.DecodeChain(blob, "wrong-password"); err == nil {
		t.Fatal("PFX accepted wrong password")
	}
	if err = CreateCertificates(data); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(blob, must(os.ReadFile(filepath.Join(dir, "client.p12")))) {
		t.Fatal("certificate rotated")
	}
	e := New(t.TempDir(), data)
	out := t.TempDir()
	if err = e.ExportClient(out); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(blob, must(os.ReadFile(filepath.Join(out, "client.p12")))) {
		t.Fatal("export differs")
	}
	if err = e.ExportClient(dir); err == nil {
		t.Fatal("same-path export accepted")
	}
	if !bytes.Equal(blob, must(os.ReadFile(filepath.Join(dir, "client.p12")))) {
		t.Fatal("same-path export destroyed certificate")
	}
	if err = os.Remove(filepath.Join(dir, "client-ca.key")); err != nil {
		t.Fatal(err)
	}
	if err = CreateCertificates(data); err == nil {
		t.Fatal("partial certificates silently replaced")
	}
	if !bytes.Equal(blob, must(os.ReadFile(filepath.Join(dir, "client.p12")))) {
		t.Fatal("partial-set failure changed PFX")
	}
}

type harness struct {
	e        *Engine
	states   map[string]string
	commands []Command
	fail     func(Command) error
	waits    []string
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	root := t.TempDir()
	data := filepath.Join(t.TempDir(), "persistent")
	check(os.MkdirAll(filepath.Join(root, "vendor"), 0700))
	check(os.WriteFile(filepath.Join(root, "vendor", "WinSW.exe"), []byte("mock wrapper"), 0600))
	check(os.MkdirAll(filepath.Join(root, "postgres", "bin"), 0700))
	check(os.WriteFile(filepath.Join(root, "postgres", "bin", "pg_dump.exe"), []byte("mock dump tool"), 0600))
	check(os.WriteFile(filepath.Join(root, "version"), []byte("0.1.18"), 0600))
	h := &harness{e: New(root, data), states: map[string]string{}}
	h.e.State = func(name string) (string, error) {
		if state, ok := h.states[name]; ok {
			return state, nil
		}
		return "missing", nil
	}
	h.e.Sleep = func(time.Duration) {}
	h.e.SID = func() (string, error) { return "S-1-5-21-123", nil }
	h.e.CheckPorts = func(Options) error { return nil }
	h.e.WaitURL = func(url string) error { h.waits = append(h.waits, url); return nil }
	h.e.Run = func(c Command) (string, error) {
		h.commands = append(h.commands, c)
		if h.fail != nil {
			if err := h.fail(c); err != nil {
				return "", err
			}
		}
		base := filepath.Base(c.Path)
		args := c.Args
		if strings.HasPrefix(base, "AISecretary") && strings.HasSuffix(base, ".exe") {
			name := strings.TrimSuffix(base, ".exe")
			switch args[0] {
			case "install":
				h.states[name] = "stopped"
			case "start":
				h.states[name] = "running"
			case "stop":
				h.states[name] = "stopped"
			case "uninstall":
				delete(h.states, name)
			}
		}
		if base == "initdb.exe" && args[0] == "-D" {
			check(os.MkdirAll(filepath.Join(data, "postgres"), 0700))
			check(os.WriteFile(filepath.Join(data, "postgres", "PG_VERSION"), []byte("17"), 0600))
		}
		if base == "pg_dump.exe" && args[0] != "--version" {
			check(os.WriteFile(args[len(args)-1], []byte("PGDMPmock"), 0600))
		}
		if base == "psql.exe" && c.Input != nil {
			return "1\n", nil
		}
		return "ok", nil
	}
	return h
}
func TestRuntimeChecksWithoutMutations(t *testing.T) {
	h := newHarness(t)
	var wrapper string
	h.fail = func(c Command) error {
		if filepath.Base(c.Path) == "WinSW.exe" {
			wrapper = c.Path
			var x struct {
				ID  string `xml:"id"`
				Exe string `xml:"executable"`
			}
			if err := xml.Unmarshal(must(os.ReadFile(strings.TrimSuffix(c.Path, ".exe")+".xml")), &x); err != nil {
				t.Fatal(err)
			}
			if x.ID != "AISecretaryRuntimeProbe" || !strings.HasSuffix(x.Exe, "improver.exe") {
				t.Fatal(x)
			}
		}
		return nil
	}
	if err := h.e.CheckRuntime(); err != nil {
		t.Fatal(err)
	}
	if len(h.commands) != 8 || exists(h.e.Data) || exists(filepath.Dir(wrapper)) {
		t.Fatal("runtime check changed persistent state or leaked files")
	}
	for _, c := range h.commands {
		if len(c.Args) != 1 || (c.Args[0] != "version" && c.Args[0] != "--version") {
			t.Fatal(c)
		}
	}
}
func TestRuntimeFailurePreventsConfiguration(t *testing.T) {
	h := newHarness(t)
	h.fail = func(Command) error { return errors.New("DLL load failure") }
	if err := h.e.Configure(DefaultOptions()); err == nil || !strings.Contains(err.Error(), "Go server") {
		t.Fatal(err)
	}
	if exists(h.e.Data) || len(h.commands) != 1 {
		t.Fatal("mutation after failed runtime probe")
	}
}
func TestConfigureUpgradeAndUninstallPreserveData(t *testing.T) {
	h := newHarness(t)
	if err := h.e.Configure(DefaultOptions()); err != nil {
		t.Fatal(err)
	}
	for _, name := range Services[:4] {
		if h.states[name] != "running" {
			t.Fatal(h.states)
		}
	}
	if len(h.waits) != 2 {
		t.Fatal(h.waits)
	}
	key := must(os.ReadFile(filepath.Join(h.e.Data, "secrets", "master-key")))
	check(os.WriteFile(filepath.Join(h.e.Data, "data", "marker"), []byte("preserved"), 0600))
	if err := h.e.Prepare("0.1.18"); err != nil {
		t.Fatal(err)
	}
	var upgrade map[string]string
	check(readJSON(filepath.Join(h.e.Data, "upgrade-state.json"), &upgrade))
	backup := upgrade["backup"]
	if upgrade["phase"] != "prepared" || !exists(filepath.Join(backup, "database.dump")) || !exists(filepath.Join(backup, "secrets", "master-key")) {
		t.Fatal(upgrade)
	}
	z, err := zip.OpenReader(filepath.Join(backup, "program.zip"))
	if err != nil {
		t.Fatal(err)
	}
	if len(z.File) == 0 {
		t.Fatal("empty program backup")
	}
	z.Close()
	for _, state := range h.states {
		if state != "stopped" {
			t.Fatal(h.states)
		}
	}
	changed := DefaultOptions()
	changed.APIPort = 18001
	if err = h.e.Configure(changed); err != nil {
		t.Fatal(err)
	}
	o := must(readOptions(h.e.Data))
	if o.APIPort != 18000 {
		t.Fatal("upgrade changed persisted ports")
	}
	if !bytes.Equal(key, must(os.ReadFile(filepath.Join(h.e.Data, "secrets", "master-key")))) {
		t.Fatal("upgrade changed encryption key")
	}
	if err = h.e.Remove(); err != nil {
		t.Fatal(err)
	}
	if len(h.states) != 0 || !exists(filepath.Join(h.e.Data, "postgres", "PG_VERSION")) || !exists(filepath.Join(h.e.Data, "data", "marker")) {
		t.Fatal("uninstall lost data or left services")
	}
}
func TestBackupFailureResumesServices(t *testing.T) {
	h := newHarness(t)
	check(h.e.Configure(DefaultOptions()))
	h.fail = func(c Command) error {
		if filepath.Base(c.Path) == "pg_dump.exe" && c.Args[0] != "--version" {
			return errors.New("dump failed")
		}
		return nil
	}
	if err := h.e.Prepare("0.1.18"); err == nil {
		t.Fatal("backup failure ignored")
	}
	for _, name := range Services[:4] {
		if h.states[name] != "running" {
			t.Fatal(h.states)
		}
	}
}
func TestDowngradeRefusedBeforeStopping(t *testing.T) {
	h := newHarness(t)
	check(h.e.Configure(DefaultOptions()))
	before := len(h.commands)
	if err := h.e.Prepare("0.1.17"); err == nil {
		t.Fatal("downgrade accepted")
	}
	if len(h.commands) != before {
		t.Fatal("downgrade changed services")
	}
}
func TestMigrationFailureLeavesApplicationStopped(t *testing.T) {
	h := newHarness(t)
	h.fail = func(c Command) error {
		if filepath.Base(c.Path) == "improver.exe" && c.Args[0] == "migrate" {
			return errors.New("migration failed")
		}
		return nil
	}
	if err := h.e.Configure(DefaultOptions()); err == nil {
		t.Fatal("migration failure ignored")
	}
	for _, name := range Services[1:] {
		if h.states[name] == "running" {
			t.Fatal(h.states)
		}
	}
	if len(h.waits) != 0 {
		t.Fatal("app startup after failed migration")
	}
	if !exists(filepath.Join(h.e.Data, "secrets", "master-key")) {
		t.Fatal("failure removed secrets")
	}
}
func TestMissingExistingSecretIsNotReplaced(t *testing.T) {
	h := newHarness(t)
	check(h.e.Configure(DefaultOptions()))
	path := filepath.Join(h.e.Data, "secrets", "master-key")
	check(os.Remove(path))
	if err := h.e.Configure(DefaultOptions()); err == nil {
		t.Fatal("missing key ignored")
	}
	if exists(path) {
		t.Fatal("key silently replaced")
	}
}
func TestBootstrapRevokesTemporaryACLAfterFailure(t *testing.T) {
	h := newHarness(t)
	h.fail = func(c Command) error {
		if filepath.Base(c.Path) == "initdb.exe" && c.Args[0] == "-D" {
			return errors.New("initdb failed")
		}
		return nil
	}
	if err := h.e.Configure(DefaultOptions()); err == nil {
		t.Fatal("bootstrap failure ignored")
	}
	revoked := false
	for _, c := range h.commands {
		revoked = revoked || (filepath.Base(c.Path) == "icacls.exe" && strings.Contains(strings.Join(c.Args, " "), "/remove:g *S-1-5-21-123"))
	}
	if !revoked {
		t.Fatal("temporary ACL leaked")
	}
}
func TestColdBackupAfterUninstall(t *testing.T) {
	h := newHarness(t)
	check(h.e.Configure(DefaultOptions()))
	check(h.e.Remove())
	check(os.RemoveAll(h.e.Root))
	h.fail = func(c Command) error {
		if c.Dir != "" {
			return fmt.Errorf("process uses a removed working directory: %s", c.Dir)
		}
		return nil
	}
	check(h.e.SecureDirectory())
	if err := h.e.Prepare("0.1.18"); err != nil {
		t.Fatal(err)
	}
	var state map[string]string
	check(readJSON(filepath.Join(h.e.Data, "upgrade-state.json"), &state))
	if state["format"] != "cold-cluster" || !exists(filepath.Join(state["backup"], "postgres", "PG_VERSION")) || !exists(filepath.Join(state["backup"], "secrets", "master-key")) {
		t.Fatal(state)
	}
}

// Run the actual process transport without OS-specific shell quoting.
func TestRunCommandRedactsFailures(t *testing.T) {
	if os.Getenv("SETUP_TEST_CHILD") == "1" {
		fmt.Fprintln(os.Stderr, "sensitive-value provider failure")
		os.Exit(12)
	}
	c := Command{Path: os.Args[0], Args: []string{"-test.run=TestRunCommandRedactsFailures"}, Env: map[string]string{"SETUP_TEST_CHILD": "1", "PGPASSWORD": "sensitive-value"}, Timeout: 5 * time.Second}
	_, err := RunCommand(c)
	if err == nil || strings.Contains(err.Error(), "sensitive-value") || !strings.Contains(err.Error(), "[redacted]") {
		t.Fatal(err)
	}
	statement := "SELECT 'sensitive-value';"
	c.Input = &statement
	_, err = RunCommand(c)
	if err == nil || strings.Contains(err.Error(), "provider failure") {
		t.Fatal(err)
	}
}
func TestJSONWritesReplaceExistingFiles(t *testing.T) {
	p := filepath.Join(t.TempDir(), "state.json")
	check(writeJSON(p, map[string]int{"n": 1}))
	check(writeJSON(p, map[string]int{"n": 2}))
	var v map[string]int
	check(json.Unmarshal(must(os.ReadFile(p)), &v))
	if v["n"] != 2 {
		t.Fatal(v)
	}
}

func TestExistingPythonCertificatesRemainCompatible(t *testing.T) {
	data := t.TempDir()
	check(copyTree(filepath.Join("testdata", "python-certificates", "certificates"), filepath.Join(data, "certificates")))
	before := map[string][]byte{}
	for _, name := range certificateFiles {
		before[name] = must(os.ReadFile(filepath.Join(data, "certificates", name)))
	}
	if err := CreateCertificates(data); err != nil {
		t.Fatal(err)
	}
	for name, blob := range before {
		if !bytes.Equal(blob, must(os.ReadFile(filepath.Join(data, "certificates", name)))) {
			t.Fatal("legacy certificate changed", name)
		}
	}
}
func TestPublicConfigureValidatesBeforeStartingProxy(t *testing.T) {
	h := newHarness(t)
	o := DefaultOptions()
	o.PublicHost = "assistant.example.org"
	validated := false
	h.fail = func(c Command) error {
		if filepath.Base(c.Path) == "caddy.exe" && c.Args[0] == "validate" {
			validated = true
		}
		if filepath.Base(c.Path) == "AISecretaryProxy.exe" && c.Args[0] == "start" && !validated {
			t.Fatal("proxy started without Caddy validation")
		}
		return nil
	}
	if err := h.e.Configure(o); err != nil {
		t.Fatal(err)
	}
	if !validated || h.states["AISecretaryProxy"] != "running" {
		t.Fatal(h.states)
	}
	config := must(readText(filepath.Join(h.e.Data, "Caddyfile")))
	if !strings.Contains(config, "require_and_verify") {
		t.Fatal("mTLS missing")
	}
	check(validateCertificates(filepath.Join(h.e.Data, "certificates")))
}
