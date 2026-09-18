package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/mumg/ai_secretary/windows/internal/setup"
)

var version = "dev"

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, "Ошибка установки:", err)
		os.Exit(1)
	}
}
func run(args []string) error {
	if len(args) == 0 {
		return errors.New("ожидается configure, prepare, remove, export-client, check-runtime или version")
	}
	command := args[0]
	if command == "version" {
		fmt.Println(version)
		return nil
	}
	switch command {
	case "configure", "prepare", "remove", "export-client", "check-runtime":
	default:
		return errors.New("неизвестная команда")
	}
	f := flag.NewFlagSet(command, flag.ContinueOnError)
	root := f.String("root", "", "program directory")
	data := f.String("data", "", "persistent data directory")
	target := f.String("target-version", version, "target release")
	output := f.String("output", "", "client certificate export directory")
	o := setup.DefaultOptions()
	f.IntVar(&o.APIPort, "api-port", o.APIPort, "API port")
	f.IntVar(&o.ParserPort, "parser-port", o.ParserPort, "parser port")
	f.IntVar(&o.DatabasePort, "database-port", o.DatabasePort, "database port")
	f.StringVar(&o.PublicHost, "public-host", "", "HTTPS hostname")
	f.StringVar(&o.LLMURL, "llm-url", o.LLMURL, "model API URL")
	if err := f.Parse(args[1:]); err != nil {
		return err
	}
	if f.NArg() != 0 || *root == "" {
		return errors.New("нужен --root и корректные параметры")
	}
	if runtime.GOOS != "windows" {
		return errors.New("установочный помощник запускается только на Windows")
	}
	program, err := filepath.Abs(*root)
	if err != nil {
		return err
	}
	if command == "check-runtime" {
		e := setup.New(program, "")
		e.Log = os.Stdout
		return e.CheckRuntime()
	}
	if err := setup.RequireAdministrator(); err != nil {
		return err
	}
	if *data == "" {
		return errors.New("нужен --data")
	}
	persistent, err := filepath.Abs(*data)
	if err != nil {
		return err
	}
	for _, pair := range [][2]string{{program, persistent}, {persistent, program}} {
		rel, err := filepath.Rel(strings.ToLower(pair[0]), strings.ToLower(pair[1]))
		if err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
			return errors.New("каталоги программы и данных не должны пересекаться")
		}
	}
	e := setup.New(program, persistent)
	if err := e.SecureDirectory(); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Join(persistent, "logs"), 0700); err != nil {
		return err
	}
	log, err := os.OpenFile(filepath.Join(persistent, "logs", "installer.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	e.Log = log
	fmt.Fprintln(log, time.Now().UTC().Format(time.RFC3339), command, "version", version)
	switch command {
	case "configure":
		err = e.Configure(o)
	case "prepare":
		err = e.Prepare(*target)
	case "remove":
		err = e.Remove()
	case "export-client":
		err = e.ExportClient(*output)
	}
	if err != nil {
		fmt.Fprintln(log, "Ошибка установки:", err)
		fmt.Fprintln(log, "Данные сохранены в", persistent)
		return errors.New("операция не завершена; проверьте logs/installer.log в каталоге данных")
	}
	return log.Sync()
}
