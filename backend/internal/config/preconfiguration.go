package config

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
)

// Preconfiguration is an installation baseline, never a database import.
// File secrets stay in memory; user-provided secrets are encrypted in the database.
type Preconfiguration struct {
	LLMAPIKey     string           `json:"llm_api_key,omitempty"`
	SchemaVersion int              `json:"schema_version"`
	Settings      Object           `json:"settings"`
	Sources       []SourceDefaults `json:"sources"`
}
type SourceDefaults struct {
	Credential string `json:"credential,omitempty"`
	ID         string `json:"id"`
	Label      string `json:"label"`
	SourceType string `json:"source_type"`
	Enabled    bool   `json:"enabled"`
	Settings   Object `json:"settings"`
}

// An explicit path is required to exist. The conventional adjacent file is optional.
func (c *Config) LoadPreconfiguration(path string, required bool) error {
	f, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) && !required {
		return nil
	}
	if err != nil {
		return errors.New("cannot open application preconfiguration")
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil || len(data) > 1<<20 {
		return errors.New("cannot read application preconfiguration (maximum 1 MiB)")
	}
	var p Preconfiguration
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(&p) != nil || p.SchemaVersion != 1 {
		return errors.New("invalid application preconfiguration: expected schema_version 1")
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return errors.New("invalid application preconfiguration: trailing JSON")
	}
	for _, r := range p.LLMAPIKey {
		if r < 33 || r > 126 {
			return errors.New("invalid preconfigured LLM API key")
		}
	}
	if len(p.LLMAPIKey) > 8192 {
		return errors.New("invalid preconfigured LLM API key")
	}
	builtins := *c
	builtins.Preconfiguration = Preconfiguration{}
	baseline := builtins.Defaults()
	// Reject misspelled settings and secrets instead of silently ignoring them.
	for section, value := range p.Settings {
		_, known := baseline[section]
		fields, valid := value.(map[string]any)
		if !known || !valid {
			return errors.New("invalid preconfiguration settings section")
		}
		for key, value := range fields {
			exemplar, ok := Section(baseline, section)[key]
			if !ok || value == nil {
				return errors.New("unknown or null preconfiguration setting")
			}
			switch exemplar.(type) {
			case string:
				if _, ok := value.(string); !ok {
					return errors.New("invalid preconfiguration string")
				}
			case bool:
				if _, ok := value.(bool); !ok {
					return errors.New("invalid preconfiguration boolean")
				}
			case float64:
				if _, ok := value.(float64); !ok {
					return errors.New("invalid preconfiguration number")
				}
			case []any:
				if _, ok := value.([]any); !ok {
					return errors.New("invalid preconfiguration array")
				}
			}
		}
	}
	if err := Validate(Merge(baseline, p.Settings)); err != nil {
		return errors.New("invalid preconfiguration settings")
	}
	ids := map[string]bool{}
	validID := regexp.MustCompile(`^[a-zA-Z0-9_-]{1,128}$`)
	fields := map[string][]string{
		"imap":           {"host", "port", "tls", "username", "inbox_folder", "sent_folder", "link_patterns"},
		"exchange":       {"ews_url", "primary_smtp_address", "username", "auth_type", "inbox_folder", "sent_folder", "link_patterns"},
		"mts_link":       {"base_url", "poll_interval_seconds", "link_patterns"},
		"external_tasks": {"link_patterns"},
	}
	for i := range p.Sources {
		s := &p.Sources[i]
		allowed, ok := fields[s.SourceType]
		if !validID.MatchString(s.ID) || ids[s.ID] || !ok || len([]rune(s.Label)) == 0 || len([]rune(s.Label)) > 255 {
			return fmt.Errorf("invalid preconfigured source at index %d", i)
		}
		ids[s.ID] = true
		if s.Enabled && s.SourceType != "external_tasks" {
			if s.Credential == "" {
				return errors.New("enabled preconfigured source requires a credential")
			}
			required := map[string][]string{"imap": {"host", "port", "username"}, "exchange": {"ews_url", "primary_smtp_address", "username"}, "mts_link": {"base_url"}}
			for _, key := range required[s.SourceType] {
				if s.Settings[key] == nil || s.Settings[key] == "" {
					return errors.New("enabled preconfigured source is incomplete")
				}
			}
		}
		if s.Settings == nil {
			s.Settings = Object{}
		}
		for k, v := range s.Settings {
			found := false
			for _, a := range allowed {
				found = found || a == k
			}
			if !found || v == nil {
				return errors.New("unknown or null preconfigured source setting")
			}
			switch k {
			case "port", "poll_interval_seconds":
				n, ok := v.(float64)
				if !ok || n != float64(int(n)) || n < 1 || n > 65535 {
					return errors.New("invalid source numeric setting")
				}
			case "tls":
				if _, ok := v.(bool); !ok {
					return errors.New("invalid source TLS setting")
				}
			case "link_patterns":
				if _, ok := v.([]any); !ok {
					return errors.New("invalid source link patterns")
				}
			default:
				if _, ok := v.(string); !ok {
					return errors.New("invalid source text setting")
				}
			}
		}
	}
	c.Preconfiguration = p
	return nil
}
func (c *Config) loadAdjacentPreconfiguration() error {
	if p := os.Getenv("APP_CONFIG_FILE"); p != "" {
		return c.LoadPreconfiguration(p, true)
	}
	// Installers may specify a stable adjacent path before policies deliver a file.
	p := os.Getenv("APP_CONFIG_DEFAULT_FILE")
	if p == "" {
		executable, err := os.Executable()
		if err != nil {
			return err
		}
		p = filepath.Join(filepath.Dir(executable), "secretary-config.json")
	}
	return c.LoadPreconfiguration(p, false)
}

const MTSLinkPattern = `^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d+)(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$`

func (c Config) SourceDefaults() Object {
	out := Object{}
	for _, s := range c.Preconfiguration.Sources {
		settings := Clone(s.Settings)
		if settings == nil {
			settings = Object{}
		}
		if _, ok := settings["link_patterns"]; !ok {
			settings["link_patterns"] = []any{}
			if s.SourceType == "mts_link" {
				settings["link_patterns"] = []any{MTSLinkPattern}
			}
		}
		out[s.ID] = Object{"label": s.Label, "source_type": s.SourceType, "enabled": s.Enabled, "settings": settings}
	}
	return out
}

// Difference preserves explicit false, empty strings and empty arrays. Objects
// are sparse; arrays are values, so the user can replace or clear a whole list.
func Difference(base, value Object) Object {
	out := Object{}
	for k, v := range value {
		b, exists := base[k]
		vb, _ := json.Marshal(v)
		bb, _ := json.Marshal(b)
		if exists && bytes.Equal(vb, bb) {
			continue
		}
		switch v.(type) {
		case Object, map[string]any:
			delta := Difference(Section(base, k), Section(value, k))
			if len(delta) > 0 {
				out[k] = delta
			}
		default:
			out[k] = v
		}
	}
	return out
}
