package setup

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type Command struct {
	Path    string
	Args    []string
	Dir     string
	Env     map[string]string
	Input   *string
	Timeout time.Duration
}
type Engine struct {
	Root, Data string
	Run        func(Command) (string, error)
	State      func(string) (string, error)
	SID        func() (string, error)
	Sleep      func(time.Duration)
	Now        func() time.Time
	CheckPorts func(Options) error
	WaitURL    func(string) error
	Log        io.Writer
}

func New(root, data string) *Engine {
	e := &Engine{Root: root, Data: data, Run: RunCommand, State: serviceState, SID: currentSID, Sleep: time.Sleep, Now: time.Now, CheckPorts: checkPorts, WaitURL: waitURL, Log: io.Discard}
	return e
}
func check(err error) {
	if err != nil {
		panic(err)
	}
}
func must[T any](v T, err error) T { check(err); return v }
func guard(fn func()) (err error) {
	defer func() {
		if v := recover(); v != nil {
			if e, ok := v.(error); ok {
				err = e
			} else {
				err = errors.New("внутренняя ошибка установщика")
			}
		}
	}()
	fn()
	return nil
}
func (e *Engine) command(path string, args ...string) Command {
	dir := e.Root
	// Reinstallation can preserve data after the program directory was removed.
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		dir = ""
	}
	return Command{Path: path, Args: args, Dir: dir, Timeout: 300 * time.Second}
}
func (e *Engine) run(path string, args ...string) string {
	return must(e.Run(e.command(path, args...)))
}
func (e *Engine) wrapper(name, command string) {
	e.run(filepath.Join(e.Root, "services", name+".exe"), command)
}
func (e *Engine) stop(name string) {
	if must(e.State(name)) != "running" {
		return
	}
	e.wrapper(name, "stop")
	for i := 0; i < 90; i++ {
		if must(e.State(name)) != "running" {
			return
		}
		e.Sleep(time.Second)
	}
	panic(fmt.Errorf("не удалось остановить %s", name))
}
func (e *Engine) SecureDirectory() error {
	return guard(func() {
		check(os.MkdirAll(e.Data, 0700))
		e.run("icacls.exe", e.Data, "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", "*S-1-5-19:(OI)(CI)M")
	})
}
func (e *Engine) databaseEnv() map[string]string {
	return map[string]string{"PGPASSWORD": must(readText(filepath.Join(e.Data, "secrets", "postgres-admin-password")))}
}
func (e *Engine) sql(o Options, statement string) string {
	c := e.command(filepath.Join(e.Root, "postgres", "bin", "psql.exe"), "-X", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", strconv.Itoa(o.DatabasePort), "-U", "postgres", "-d", "postgres", "-t", "-A")
	c.Env = e.databaseEnv()
	c.Input = &statement
	return must(e.Run(c))
}
func (e *Engine) waitDatabase(o Options) {
	for i := 0; i < 90; i++ {
		if guard(func() { e.sql(o, "SELECT 1;") }) == nil {
			return
		}
		e.Sleep(time.Second)
	}
	panic(errors.New("PostgreSQL не запустился; проверьте журнал AISecretaryDatabase"))
}
func (e *Engine) CheckRuntime() error {
	return guard(func() {
		dir := must(os.MkdirTemp("", "secretary-runtime-"))
		defer os.RemoveAll(dir)
		wrapper := filepath.Join(dir, "WinSW.exe")
		check(copyFile(filepath.Join(e.Root, "vendor", "WinSW.exe"), wrapper))
		config := must(xmlDocument(node("service", "", node("id", "AISecretaryRuntimeProbe"), node("name", "Runtime probe"), node("description", "Version check only; never installed as a service"), node("executable", filepath.Join(e.Root, "backend", "improver.exe")), node("logpath", dir))))
		check(os.WriteFile(filepath.Join(dir, "WinSW.xml"), []byte(config), 0600))
		probes := []struct{ name, path, arg string }{
			{"Go server", filepath.Join(e.Root, "backend", "improver.exe"), "version"}, {"Go document parser", filepath.Join(e.Root, "parser", "document-parser.exe"), "version"},
			{"PostgreSQL", filepath.Join(e.Root, "postgres", "bin", "postgres.exe"), "--version"}, {"PostgreSQL initdb", filepath.Join(e.Root, "postgres", "bin", "initdb.exe"), "--version"}, {"PostgreSQL client", filepath.Join(e.Root, "postgres", "bin", "psql.exe"), "--version"}, {"PostgreSQL backup", filepath.Join(e.Root, "postgres", "bin", "pg_dump.exe"), "--version"},
			{"WinSW", wrapper, "version"}, {"Caddy", filepath.Join(e.Root, "caddy", "caddy.exe"), "version"},
		}
		for _, p := range probes {
			c := e.command(p.path, p.arg)
			c.Timeout = 60 * time.Second
			if _, err := e.Run(c); err != nil {
				panic(fmt.Errorf("%s: %w", p.name, err))
			}
			fmt.Fprintln(e.Log, p.name+": OK")
		}
	})
}
func (e *Engine) Configure(proposed Options) error {
	// Probe before touching persistent data or service registrations.
	if err := e.CheckRuntime(); err != nil {
		return err
	}
	err := guard(func() { e.configure(proposed) })
	if err != nil {
		for i := len(Services) - 1; i >= 1; i-- {
			name := Services[i]
			_ = guard(func() {
				e.stop(name)
				if must(e.State(name)) != "missing" {
					e.run("sc.exe", "config", name, "start=", "demand")
				}
			})
		}
	}
	return err
}
func (e *Engine) configure(proposed Options) {
	check(e.SecureDirectory())
	fresh := !exists(filepath.Join(e.Data, "connection.json"))
	var o Options
	if fresh {
		o = must(proposed.Validate())
	} else {
		o = must(readOptions(e.Data))
	}
	for _, n := range []string{"secrets", "data", "logs", "certificates", "caddy", "backups"} {
		check(os.MkdirAll(filepath.Join(e.Data, n), 0700))
	}
	if fresh {
		check(e.CheckPorts(o))
		check(writeJSON(filepath.Join(e.Data, "connection.json"), o))
	}
	for _, n := range []string{"master-key", "database-password", "postgres-admin-password"} {
		path := filepath.Join(e.Data, "secrets", n)
		if exists(path) {
			if must(readText(path)) == "" {
				panic(errors.New("пустой ключ или пароль; восстановите secrets из резервной копии"))
			}
			continue
		}
		if exists(filepath.Join(e.Data, "postgres", "PG_VERSION")) {
			panic(errors.New("отсутствует ключ или пароль существующей установки; восстановите secrets из копии"))
		}
		check(writeAtomic(path, []byte(must(secret(48)))))
	}
	// Refuse unsupported clusters before stopping or re-registering services.
	if exists(filepath.Join(e.Data, "postgres", "PG_VERSION")) && must(readText(filepath.Join(e.Data, "postgres", "PG_VERSION"))) != "17" {
		panic(errors.New("версия PostgreSQL требует отдельной миграции; каталог базы не изменён"))
	}
	active := Services
	if o.PublicHost == "" {
		active = Services[:len(Services)-1]
	}
	check(os.MkdirAll(filepath.Join(e.Root, "services"), 0700))
	for i := len(Services) - 1; i >= 0; i-- {
		e.stop(Services[i])
	}
	for _, name := range active {
		if must(e.State(name)) != "missing" {
			e.wrapper(name, "uninstall")
		}
		check(copyFile(filepath.Join(e.Root, "vendor", "WinSW.exe"), filepath.Join(e.Root, "services", name+".exe")))
		check(writeAtomic(filepath.Join(e.Root, "services", name+".xml"), []byte(must(ServiceXML(e.Root, e.Data, o, name)))))
	}
	if !exists(filepath.Join(e.Data, "postgres", "PG_VERSION")) {
		sid := must(e.SID())
		temporary := sid != "S-1-5-18" && sid != "S-1-5-19"
		func() {
			if temporary {
				e.run("icacls.exe", e.Data, "/grant:r", "*"+sid+":(OI)(CI)M")
				defer func() { e.run("icacls.exe", e.Data, "/remove:g", "*"+sid, "/T", "/Q") }()
			}
			e.run(filepath.Join(e.Root, "postgres", "bin", "initdb.exe"), "-D", filepath.Join(e.Data, "postgres"), "-U", "postgres", "--pwfile", filepath.Join(e.Data, "secrets", "postgres-admin-password"), "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C")
			e.run("icacls.exe", filepath.Join(e.Data, "postgres"), "/grant", "*S-1-5-19:(OI)(CI)M", "/T", "/Q")
		}()
	}
	for _, name := range active {
		e.wrapper(name, "install")
	}
	e.wrapper("AISecretaryDatabase", "start")
	e.waitDatabase(o)
	if strings.TrimSpace(e.sql(o, "SELECT 1 FROM pg_roles WHERE rolname='improver';")) != "1" {
		password := strings.ReplaceAll(must(readText(filepath.Join(e.Data, "secrets", "database-password"))), "'", "''")
		e.sql(o, "CREATE ROLE improver LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '"+password+"';")
	}
	if strings.TrimSpace(e.sql(o, "SELECT 1 FROM pg_database WHERE datname='improver';")) != "1" {
		e.sql(o, "CREATE DATABASE improver OWNER improver;")
	}
	check(writeJSON(filepath.Join(e.Data, "upgrade-state.json"), map[string]string{"phase": "migrating"}))
	migrate := e.command(filepath.Join(e.Root, "backend", "improver.exe"), "migrate")
	migrate.Dir = filepath.Join(e.Root, "backend")
	migrate.Env = Environment(e.Data, o)
	must(e.Run(migrate))
	e.wrapper("AISecretaryParser", "start")
	check(e.WaitURL(fmt.Sprintf("http://127.0.0.1:%d/health", o.ParserPort)))
	e.wrapper("AISecretaryApi", "start")
	check(e.WaitURL(fmt.Sprintf("http://127.0.0.1:%d/health/ready", o.APIPort)))
	if o.PublicHost != "" {
		check(CreateCertificates(e.Data))
		check(writeAtomic(filepath.Join(e.Data, "Caddyfile"), []byte(CaddyConfig(e.Data, o))))
		e.run(filepath.Join(e.Root, "caddy", "caddy.exe"), "validate", "--config", filepath.Join(e.Data, "Caddyfile"), "--adapter", "caddyfile")
		for _, port := range []string{"80", "443"} {
			rule := "AISecretary-HTTPS-" + port
			e.Run(e.command("netsh.exe", "advfirewall", "firewall", "delete", "rule", "name="+rule))
			e.run("netsh.exe", "advfirewall", "firewall", "add", "rule", "name="+rule, "dir=in", "action=allow", "protocol=TCP", "localport="+port, "program="+filepath.Join(e.Root, "caddy", "caddy.exe"))
		}
		e.wrapper("AISecretaryProxy", "start")
	}
	e.wrapper("AISecretaryWorker", "start")
	version := must(readText(filepath.Join(e.Root, "version")))
	must(versionParts(version))
	check(writeJSON(filepath.Join(e.Data, "installed.json"), map[string]any{"version": version, "root": e.Root, "services": active}))
	check(writeJSON(filepath.Join(e.Data, "upgrade-state.json"), map[string]string{"phase": "complete", "version": version}))
	for filename, path := range map[string]string{"Open.url": "/app", "Settings.url": "/admin"} {
		check(writeAtomic(filepath.Join(e.Root, filename), []byte(fmt.Sprintf("[InternetShortcut]\r\nURL=http://127.0.0.1:%d%s\r\n", o.APIPort, path))))
	}
	fmt.Fprintln(e.Log, "Службы установлены. Настройте источники и модель в панели администратора.")
}
func (e *Engine) Prepare(target string) error {
	return guard(func() {
		must(versionParts(target))
		if !exists(filepath.Join(e.Data, "connection.json")) {
			return
		}
		o := must(readOptions(e.Data))
		if exists(filepath.Join(e.Data, "installed.json")) {
			var installed struct{ Version string }
			check(readJSON(filepath.Join(e.Data, "installed.json"), &installed))
			if must(downgrade(target, installed.Version)) {
				panic(errors.New("установка более старой версии запрещена"))
			}
		}
		running := []string{}
		for _, name := range Services {
			if must(e.State(name)) == "running" {
				running = append(running, name)
			}
		}
		// Reinstall after uninstall: no services or old executables remain. Take
		// a cold cluster backup before new files or migrations touch saved data.
		if !exists(filepath.Join(e.Root, "postgres", "bin", "pg_dump.exe")) {
			for _, name := range Services {
				if must(e.State(name)) != "missing" {
					panic(errors.New("отсутствуют программы существующей службы; восстановите каталог программы"))
				}
			}
			check(os.MkdirAll(filepath.Join(e.Data, "backups"), 0700))
			backup := must(os.MkdirTemp(filepath.Join(e.Data, "backups"), e.Now().UTC().Format("20060102T150405")+"-cold-"))
			for _, name := range []string{"postgres", "connection.json", "installed.json", "secrets", "certificates", "data", "Caddyfile", "caddy"} {
				src := filepath.Join(e.Data, name)
				info, err := os.Stat(src)
				if errors.Is(err, os.ErrNotExist) {
					continue
				}
				check(err)
				if info.IsDir() {
					check(copyTree(src, filepath.Join(backup, name)))
				} else {
					check(copyFile(src, filepath.Join(backup, name)))
				}
			}
			check(writeJSON(filepath.Join(e.Data, "upgrade-state.json"), map[string]string{"phase": "prepared", "backup": backup, "format": "cold-cluster"}))
			fmt.Fprintln(e.Log, "Холодная резервная копия создана:", backup)
			return
		}
		prepared := false
		defer func() {
			if !prepared {
				for _, name := range running {
					_ = guard(func() {
						if must(e.State(name)) != "running" {
							e.wrapper(name, "start")
						}
					})
				}
			}
		}()
		check(os.MkdirAll(filepath.Join(e.Data, "backups"), 0700))
		backup := must(os.MkdirTemp(filepath.Join(e.Data, "backups"), e.Now().UTC().Format("20060102T150405")+"-"))
		for i := len(Services) - 1; i >= 1; i-- {
			e.stop(Services[i])
		}
		if must(e.State("AISecretaryDatabase")) != "running" {
			e.wrapper("AISecretaryDatabase", "start")
		}
		e.waitDatabase(o)
		dump := e.command(filepath.Join(e.Root, "postgres", "bin", "pg_dump.exe"), "-h", "127.0.0.1", "-p", strconv.Itoa(o.DatabasePort), "-U", "postgres", "-d", "improver", "--format=custom", "--no-owner", "--no-acl", "-f", filepath.Join(backup, "database.dump"))
		dump.Env = e.databaseEnv()
		must(e.Run(dump))
		info := must(os.Stat(filepath.Join(backup, "database.dump")))
		if info.Size() == 0 {
			panic(errors.New("пустая резервная копия базы"))
		}
		for _, name := range []string{"connection.json", "installed.json", "secrets", "certificates", "data", "Caddyfile", "caddy"} {
			source := filepath.Join(e.Data, name)
			info, err := os.Stat(source)
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			check(err)
			if info.IsDir() {
				check(copyTree(source, filepath.Join(backup, name)))
			} else {
				check(copyFile(source, filepath.Join(backup, name)))
			}
		}
		check(archiveProgram(e.Root, filepath.Join(backup, "program.zip")))
		e.stop("AISecretaryDatabase")
		check(writeJSON(filepath.Join(e.Data, "upgrade-state.json"), map[string]string{"phase": "prepared", "backup": backup}))
		prepared = true
		fmt.Fprintln(e.Log, "Резервная копия создана:", backup)
	})
}
func (e *Engine) Remove() error {
	return guard(func() {
		for i := len(Services) - 1; i >= 0; i-- {
			name := Services[i]
			if must(e.State(name)) != "missing" {
				e.stop(name)
				e.wrapper(name, "uninstall")
			}
		}
		for _, port := range []string{"80", "443"} {
			e.Run(e.command("netsh.exe", "advfirewall", "firewall", "delete", "rule", "name=AISecretary-HTTPS-"+port))
		}
		fmt.Fprintln(e.Log, "Службы удалены. База, настройки и резервные копии сохранены в", e.Data)
	})
}
func (e *Engine) ExportClient(output string) error {
	return guard(func() {
		if output == "" {
			panic(errors.New("нужен каталог --output для экспорта сертификата"))
		}
		check(validateCertificates(filepath.Join(e.Data, "certificates")))
		check(os.MkdirAll(output, 0700))
		for _, name := range []string{"client.p12", "client-password.txt"} {
			check(copyFile(filepath.Join(e.Data, "certificates", name), filepath.Join(output, name)))
		}
		fmt.Fprintln(e.Log, "Сертификат и пароль сохранены в", output)
	})
}
func checkPorts(o Options) error {
	listeners := []net.Listener{}
	defer func() {
		for _, l := range listeners {
			l.Close()
		}
	}()
	for _, p := range []int{o.APIPort, o.ParserPort, o.DatabasePort} {
		l, err := net.Listen("tcp4", fmt.Sprintf("127.0.0.1:%d", p))
		if err != nil {
			return fmt.Errorf("порт %d занят", p)
		}
		listeners = append(listeners, l)
	}
	return nil
}
func waitURL(url string) error {
	client := http.Client{Timeout: 3 * time.Second}
	deadline := time.Now().Add(120 * time.Second)
	for time.Now().Before(deadline) {
		r, err := client.Get(url)
		if err == nil {
			var result map[string]any
			decode := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&result)
			r.Body.Close()
			if r.StatusCode == 200 && decode == nil && (result["status"] == "ok" || result["status"] == "ready") {
				return nil
			}
		}
		time.Sleep(time.Second)
	}
	return fmt.Errorf("компонент не готов: %s; проверьте папку logs", url)
}
func RunCommand(c Command) (string, error) {
	timeout := c.Timeout
	if timeout == 0 {
		timeout = 300 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, c.Path, c.Args...)
	cmd.Dir = c.Dir
	env := map[string]string{}
	for _, item := range os.Environ() {
		k, v, ok := strings.Cut(item, "=")
		if ok {
			env[strings.ToUpper(k)] = v
		}
	}
	for k, v := range c.Env {
		env[strings.ToUpper(k)] = v
	}
	keys := []string{}
	for k := range env {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		cmd.Env = append(cmd.Env, k+"="+env[k])
	}
	if c.Input != nil {
		cmd.Stdin = strings.NewReader(*c.Input)
	}
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err == nil {
		return string(out), nil
	}
	detail := strings.TrimSpace(stderr.String())
	if c.Input != nil {
		detail = "SQL command failed"
	} else {
		for k, v := range env {
			if v != "" && (strings.Contains(k, "PASSWORD") || strings.Contains(k, "TOKEN") || strings.Contains(k, "SECRET") || strings.Contains(k, "KEY")) {
				detail = strings.ReplaceAll(detail, v, "[redacted]")
			}
		}
	}
	if len(detail) > 1000 {
		detail = detail[:1000]
	}
	if ctx.Err() != nil {
		return "", fmt.Errorf("%s: превышено время ожидания", filepath.Base(c.Path))
	}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return "", fmt.Errorf("%s: код %d; %s", filepath.Base(c.Path), exit.ExitCode(), detail)
	}
	return "", fmt.Errorf("не удалось запустить %s", filepath.Base(c.Path))
}
