package setup

import (
	"archive/zip"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/mumg/ai_secretary/windows/internal/i18n"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func exists(path string) bool { _, err := os.Stat(path); return err == nil }
func readText(path string) (string, error) {
	b, err := os.ReadFile(path)
	return strings.TrimSpace(string(b)), err
}
func writeAtomic(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".secretary-*")
	if err != nil {
		return err
	}
	name := f.Name()
	defer os.Remove(name)
	if _, err = f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(name, path)
}
func writeJSON(path string, value any) error {
	b, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return writeAtomic(path, append(b, '\n'))
}
func readJSON(path string, value any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, value)
}
func readOptions(data string) (Options, error) {
	var o Options
	if err := readJSON(filepath.Join(data, "connection.json"), &o); err != nil {
		return o, err
	}
	return o.Validate()
}
func secret(n int) (string, error) {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
func versionParts(v string) ([3]uint64, error) {
	var parts [3]uint64
	a := strings.Split(strings.TrimSpace(v), ".")
	if len(a) != 3 {
		return parts, errors.New(i18n.Tr("некорректный номер версии"))
	}
	for i, s := range a {
		if s == "" || strings.ContainsAny(s, "+- ") {
			return parts, errors.New(i18n.Tr("некорректный номер версии"))
		}
		n, e := strconv.ParseUint(s, 10, 64)
		if e != nil {
			return parts, errors.New(i18n.Tr("некорректный номер версии"))
		}
		parts[i] = n
	}
	return parts, nil
}
func downgrade(target, current string) (bool, error) {
	a, e := versionParts(target)
	if e != nil {
		return false, e
	}
	b, e := versionParts(current)
	if e != nil {
		return false, e
	}
	for i := range a {
		if a[i] != b[i] {
			return a[i] < b[i], nil
		}
	}
	return false, nil
}
func copyFile(src, dst string) error {
	info, err := os.Lstat(src)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() {
		return fmt.Errorf(i18n.Tr("ожидался обычный файл: %s"), src)
	}
	if target, statErr := os.Stat(dst); statErr == nil && os.SameFile(info, target) {
		return errors.New(i18n.Tr("исходный файл совпадает с целевым"))
	} else if statErr != nil && !errors.Is(statErr, os.ErrNotExist) {
		return statErr
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err = os.MkdirAll(filepath.Dir(dst), 0700); err != nil {
		return err
	}
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	_, err = io.Copy(out, in)
	if err != nil {
		out.Close()
		return err
	}
	return out.Close()
}
func copyTree(src, dst string) error {
	return filepath.WalkDir(src, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&os.ModeSymlink != 0 {
			return fmt.Errorf(i18n.Tr("ссылки не поддерживаются в резервной копии: %s"), path)
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0700)
		}
		return copyFile(path, target)
	})
}
func archiveProgram(root, destination string) error {
	f, err := os.OpenFile(destination, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	z := zip.NewWriter(f)
	err = filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.Type()&os.ModeSymlink != 0 {
			return errors.New(i18n.Tr("ссылка в каталоге программы; резервная копия остановлена"))
		}
		if d.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		w, err := z.CreateHeader(&zip.FileHeader{Name: filepath.ToSlash(rel), Method: zip.Store})
		if err != nil {
			return err
		}
		in, err := os.Open(path)
		if err != nil {
			return err
		}
		_, err = io.Copy(w, in)
		closeErr := in.Close()
		return errors.Join(err, closeErr)
	})
	return errors.Join(err, z.Close(), f.Close())
}
