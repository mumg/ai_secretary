package setup

import (
	"bytes"
	"encoding/xml"
	"errors"
	"fmt"
	"net/url"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

var Services = []string{"AISecretaryDatabase", "AISecretaryParser", "AISecretaryApi", "AISecretaryWorker", "AISecretaryProxy"}

type Options struct {
	APIPort      int    `json:"api_port"`
	ParserPort   int    `json:"parser_port"`
	DatabasePort int    `json:"database_port"`
	PublicHost   string `json:"public_host"`
	LLMURL       string `json:"llm_url"`
}

func DefaultOptions() Options {
	return Options{APIPort: 18000, ParserPort: 18080, DatabasePort: 15432, LLMURL: "http://127.0.0.1:11434"}
}
func (o Options) Validate() (Options, error) {
	seen := map[int]bool{}
	for _, p := range []int{o.APIPort, o.ParserPort, o.DatabasePort} {
		if p < 1024 || p > 65535 || seen[p] {
			return o, errors.New("укажите три разных порта от 1024 до 65535")
		}
		seen[p] = true
	}
	o.PublicHost = strings.ToLower(strings.TrimSpace(o.PublicHost))
	if o.PublicHost != "" {
		valid := regexp.MustCompile(`^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$`)
		if len(o.PublicHost) > 253 || !valid.MatchString(o.PublicHost) {
			return o, errors.New("для HTTPS нужен публичный DNS-домен без протокола и пути")
		}
		for _, s := range []string{".local", ".localhost", ".invalid"} {
			if strings.HasSuffix(o.PublicHost, s) {
				return o, errors.New("нужен публичный DNS-домен")
			}
		}
	}
	for _, c := range o.LLMURL {
		if c < 32 || c == 127 || c == '\\' || c == '"' {
			return o, errors.New("адрес LLM содержит недопустимые символы")
		}
	}
	u, err := url.Parse(o.LLMURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" {
		return o, errors.New("адрес LLM должен быть HTTP(S) URL без паролей и параметров")
	}
	o.LLMURL = strings.TrimRight(o.LLMURL, "/")
	return o, nil
}
func Environment(data string, o Options) map[string]string {
	public := fmt.Sprintf("http://127.0.0.1:%d", o.APIPort)
	if o.PublicHost != "" {
		public = "https://" + o.PublicHost
	}
	return map[string]string{
		"CLIENT_ISSUER_CERT_FILE": filepath.Join(data, "certificates", "client-ca.pem"),
		"CLIENT_ISSUER_KEY_FILE":  filepath.Join(data, "certificates", "client-ca.key"),
		"DATABASE_URL":            fmt.Sprintf("postgresql://improver@127.0.0.1:%d/improver", o.DatabasePort),
		"DATABASE_PASSWORD_FILE":  filepath.Join(data, "secrets", "database-password"), "APP_MASTER_KEY_FILE": filepath.Join(data, "secrets", "master-key"),
		"DATA_DIR": filepath.Join(data, "data"), "PUBLIC_URL": public, "LOCAL_WEB_ONLY": strconv.FormatBool(o.PublicHost == ""),
		"DOCUMENT_PARSER_URL": fmt.Sprintf("http://127.0.0.1:%d", o.ParserPort), "OLLAMA_BASE_URL": o.LLMURL,
	}
}

// Quote using the Windows CommandLineToArgvW/CRT backslash rules, including
// empty arguments and paths ending in a backslash.
func CommandLine(args ...string) string {
	out := make([]string, len(args))
	for i, arg := range args {
		if arg != "" && !strings.ContainsAny(arg, " \t\n\v\"") {
			out[i] = arg
			continue
		}
		var b strings.Builder
		b.WriteByte('"')
		slashes := 0
		for _, r := range arg {
			if r == '\\' {
				slashes++
				continue
			}
			if r == '"' {
				b.WriteString(strings.Repeat("\\", slashes*2+1))
			} else {
				b.WriteString(strings.Repeat("\\", slashes))
			}
			slashes = 0
			b.WriteRune(r)
		}
		b.WriteString(strings.Repeat("\\", slashes*2))
		b.WriteByte('"')
		out[i] = b.String()
	}
	return strings.Join(out, " ")
}

type element struct {
	XMLName  xml.Name
	Attr     []xml.Attr `xml:",any,attr"`
	Text     string     `xml:",chardata"`
	Children []element  `xml:",any"`
}

func node(name, text string, children ...element) element {
	return element{XMLName: xml.Name{Local: name}, Text: text, Children: children}
}
func attributed(name string, attrs map[string]string, children ...element) element {
	e := node(name, "", children...)
	keys := []string{}
	for k := range attrs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		e.Attr = append(e.Attr, xml.Attr{Name: xml.Name{Local: k}, Value: attrs[k]})
	}
	return e
}
func xmlDocument(e element) (string, error) {
	var b bytes.Buffer
	b.WriteString(xml.Header)
	enc := xml.NewEncoder(&b)
	enc.Indent("", "  ")
	if err := enc.Encode(e); err != nil {
		return "", err
	}
	return b.String() + "\n", nil
}
func ServiceXML(root, data string, o Options, name string) (string, error) {
	found := false
	for _, s := range Services {
		found = found || s == name
	}
	if !found {
		return "", errors.New("unknown service")
	}
	fields := []element{node("id", name), node("name", strings.Replace(name, "AISecretary", "AI Секретарь — ", 1)), node("description", "Компонент AI Секретаря. Данные и конфигурация находятся в ProgramData."), node("startmode", "Automatic"), node("delayedAutoStart", "true"), node("stoptimeout", "60sec"), node("logpath", filepath.Join(data, "logs")), attributed("log", map[string]string{"mode": "roll-by-size"}, node("sizeThreshold", "10240"), node("keepFiles", "5")), attributed("onfailure", map[string]string{"action": "restart", "delay": "10sec"}), node("serviceaccount", "", node("domain", "NT AUTHORITY"), node("user", "LocalService"), node("password", "")), node("workingdirectory", filepath.Join(root, "backend"))}
	add := func(k, v string) { fields = append(fields, node(k, v)) }
	switch name {
	case "AISecretaryDatabase":
		add("executable", filepath.Join(root, "postgres", "bin", "postgres.exe"))
		add("startarguments", CommandLine("-D", filepath.Join(data, "postgres"), "-p", strconv.Itoa(o.DatabasePort), "-h", "127.0.0.1"))
		add("stopexecutable", filepath.Join(root, "postgres", "bin", "pg_ctl.exe"))
		add("stoparguments", CommandLine("stop", "-D", filepath.Join(data, "postgres"), "-m", "fast", "-w", "-t", "45"))
	case "AISecretaryProxy":
		add("executable", filepath.Join(root, "caddy", "caddy.exe"))
		add("arguments", CommandLine("run", "--config", filepath.Join(data, "Caddyfile"), "--adapter", "caddyfile"))
		add("depend", "AISecretaryApi")
		for _, k := range []string{"XDG_DATA_HOME", "XDG_CONFIG_HOME"} {
			fields = append(fields, attributed("env", map[string]string{"name": k, "value": filepath.Join(data, "caddy")}))
		}
	default:
		var args []string
		if name == "AISecretaryParser" {
			add("executable", filepath.Join(root, "parser", "document-parser.exe"))
			args = []string{"serve", "--listen", fmt.Sprintf("127.0.0.1:%d", o.ParserPort)}
		} else {
			add("depend", "AISecretaryDatabase")
			add("depend", "AISecretaryParser")
			add("executable", filepath.Join(root, "backend", "improver.exe"))
			args = []string{"worker"}
			if name == "AISecretaryApi" {
				args = []string{"serve", "--listen", fmt.Sprintf("127.0.0.1:%d", o.APIPort), "--web-dir", filepath.Join(root, "backend", "web")}
			}
		}
		add("arguments", CommandLine(args...))
		env := Environment(data, o)
		keys := []string{}
		for k := range env {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			fields = append(fields, attributed("env", map[string]string{"name": k, "value": env[k]}))
		}
	}
	return xmlDocument(node("service", "", fields...))
}
func CaddyConfig(data string, o Options) string {
	ca := strings.ReplaceAll(filepath.Join(data, "certificates", "client-ca.pem"), "\\", "/")
	return fmt.Sprintf(`{
    admin off
}
%s {
    tls {
        issuer acme {
            disable_tlsalpn_challenge
        }
        client_auth {
            mode require_and_verify
            trust_pool file {
                pem_file %s
            }
        }
    }
    encode gzip
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy no-referrer
    }
    reverse_proxy 127.0.0.1:%d
}
`, o.PublicHost, strconv.Quote(ca), o.APIPort)
}
