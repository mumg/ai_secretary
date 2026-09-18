package server

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const gateway = "https://gw.mts-link.ru"
const tokenPrefix = "mts-link-tokens:v1:"

func requestJSON(ctx context.Context, method, endpoint string, body any, headers map[string]string) (M, int, error) {
	var data []byte
	var e error
	if body != nil {
		data, e = json.Marshal(body)
		if e != nil {
			return nil, 0, e
		}
	}
	req, e := http.NewRequestWithContext(ctx, method, endpoint, bytes.NewReader(data))
	if e != nil {
		return nil, 0, errors.New("invalid endpoint")
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := http.Client{Timeout: 45 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	response, e := client.Do(req)
	if e != nil {
		return nil, 0, errors.New("upstream request failed")
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return nil, response.StatusCode, fmt.Errorf("upstream HTTP %d", response.StatusCode)
	}
	var result M
	if e = json.NewDecoder(io.LimitReader(response.Body, 32<<20)).Decode(&result); e != nil {
		return nil, response.StatusCode, errors.New("invalid upstream JSON")
	}
	return result, response.StatusCode, nil
}
func (q *request) sourceTokens(row M) (string, string) {
	encrypted := str(row, "credential_encrypted")
	if encrypted == "" {
		return "", ""
	}
	plain := must(q.server.Config.Decrypt(encrypted))
	if row["source_type"] == "mts_link" && strings.HasPrefix(plain, tokenPrefix) {
		var value M
		check(json.Unmarshal([]byte(strings.TrimPrefix(plain, tokenPrefix)), &value))
		return str(value, "access_token"), str(value, "refresh_token")
	}
	return plain, ""
}
func (q *request) ssoSource() M {
	row := q.one("SELECT * FROM communication_sources WHERE id=$1 FOR UPDATE", q.r.PathValue("source"))
	if row["source_type"] != "mts_link" || obj(row, "settings")["base_url"] != gateway {
		fail(422, "SSO поддерживается для шлюза https://gw.mts-link.ru")
	}
	return row
}
func organizations(ctx context.Context, email string) []M {
	payload, _, e := requestJSON(ctx, "POST", gateway+"/ssoExternal/ExternalSSO.GetLoginOrganizationsByEmail", M{"email": email}, nil)
	if e != nil {
		fail(502, "Не удалось получить организации SSO из МТС Линк")
	}
	out := []M{}
	items, ok := obj(payload, "value")["items"].([]any)
	if !ok {
		fail(502, "Некорректный ответ SSO")
	}
	for _, v := range items {
		if m, ok := v.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}
func ssoFingerprint(row M) string {
	return hash([]any{row["source_type"], row["settings"], row["credential_encrypted"], row["enabled"], row["created_at"]})
}
func tokenExchange(ctx context.Context, base, method string, body M) (M, error) {
	result, _, e := requestJSON(ctx, "POST", strings.TrimRight(base, "/")+"/accountUcaas/AccountUcaas."+method, body, nil)
	if e != nil {
		return nil, errors.New("МТС Линк отклонил вход. Выполните SSO-вход заново.")
	}
	v := obj(result, "value")
	if result["type"] != "Tokens" {
		return nil, errors.New("МТС Линк не вернул пару токенов")
	}
	for _, k := range []string{"accessToken", "refreshToken"} {
		s := str(v, k)
		if len(s) == 0 || len(s) > 32768 {
			return nil, errors.New("invalid token response")
		}
		for _, c := range s {
			if c < 33 || c > 126 {
				return nil, errors.New("invalid token response")
			}
		}
	}
	return M{"access_token": v["accessToken"], "refresh_token": v["refreshToken"]}, nil
}
func (s *Server) mtsAuthRoutes() {
	s.route("POST /api/v1/admin/sources/{source}/mts-link/organizations", true, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		q.ssoSource()
		m := q.body()
		textField(m, "email", 3, 320, true)
		out := []M{}
		for _, org := range organizations(q.Context, str(m, "email")) {
			if methods, ok := org["methods"].([]any); ok {
				for i, v := range methods {
					if method, ok := v.(map[string]any); ok {
						kind := ""
						switch method["type"] {
						case "SAMLLoginMethod":
							kind = "SAML"
						case "OAuthLoginMethod":
							kind = "OAuth"
						}
						if kind != "" {
							out = append(out, M{"organization_id": fmt.Sprint(org["id"]), "method_index": i, "name": org["name"], "kind": kind})
						}
					}
				}
			}
		}
		return M{"choices": out}
	})
	s.route("GET /api/v1/admin/sources/{source}/mts-link/login-email", true, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		row := q.ssoSource()
		access, _ := q.sourceTokens(row)
		access = strings.TrimSpace(strings.TrimPrefix(access, "Bearer "))
		if access == "" {
			return M{"email": nil}
		}
		payload, _, e := requestJSON(q.Context, "POST", gateway+"/accountUcaas/AccountUcaas.GetLoginData", nil, map[string]string{"Authorization": "Bearer " + access, "Cookie": "access=" + access})
		if e != nil || payload["type"] != "LoginData" {
			return M{"email": nil}
		}
		return M{"email": obj(payload, "value")["email"]}
	})
	s.route("POST /api/v1/admin/sources/{source}/mts-link/start", true, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		row := q.ssoSource()
		m := q.body()
		textField(m, "email", 3, 320, true)
		textField(m, "organization_id", 1, 256, true)
		index := int(num(m, "method_index"))
		if index < 0 || index > 100 {
			fail(422, "Invalid method index")
		}
		var chosen M
		for _, org := range organizations(q.Context, str(m, "email")) {
			if fmt.Sprint(org["id"]) == m["organization_id"] {
				if methods, ok := org["methods"].([]any); ok && index < len(methods) {
					if item, ok := methods[index].(map[string]any); ok {
						chosen = item
					}
				}
			}
		}
		if chosen == nil {
			fail(422, "Способ SSO изменился. Выберите организацию заново.")
		}
		value := obj(chosen, "value")
		query := url.Values{"email": {str(m, "email")}, "params": {str(value, "params")}, "returnUrl": {"mtslink://mobile/login"}}
		path := ""
		switch chosen["type"] {
		case "SAMLLoginMethod":
			path = "/sso/saml/login"
			query.Set("token", str(value, "connectionToken"))
		case "OAuthLoginMethod":
			path = "/sso/oauth/login"
			query.Set("id", str(value, "clientId"))
		default:
			fail(422, "Unsupported SSO method")
		}
		enabled := true
		if _, ok := m["enable_source"]; ok {
			enabled = boolean(m, "enable_source")
		}
		payload := M{"purpose": "mts-link-sso", "source_id": row["id"], "fingerprint": ssoFingerprint(row), "expires": time.Now().Add(15 * time.Minute).Unix(), "enable_source": enabled}
		ticket := must(q.server.Config.Encrypt(string(must(json.Marshal(payload)))))
		return M{"ticket": ticket, "authorization_url": gateway + path + "?" + query.Encode()}
	})
	s.route("POST /api/v1/admin/sources/{source}/mts-link/finish", true, func(q *request) any {
		q.w.Header().Set("Cache-Control", "no-store")
		m := q.body()
		plain, e := q.server.Config.Decrypt(str(m, "ticket"))
		var ticket M
		if e != nil || json.Unmarshal([]byte(plain), &ticket) != nil || ticket["purpose"] != "mts-link-sso" || ticket["source_id"] != q.r.PathValue("source") || num(ticket, "expires") <= float64(time.Now().Unix()) {
			fail(409, "Сеанс входа истёк. Начните SSO-вход заново.")
		}
		textField(m, "auth_code", 1, 32768, true)
		for _, c := range str(m, "auth_code") {
			if c < 33 || c > 126 {
				fail(422, "Некорректный код входа")
			}
		}
		row := q.ssoSource()
		if ssoFingerprint(row) != ticket["fingerprint"] {
			fail(409, "Источник изменился. Начните SSO-вход заново.")
		}
		tokens, e := tokenExchange(q.Context, gateway, "LoginByAuthCode", M{"authCode": m["auth_code"]})
		if e != nil {
			fail(502, e.Error())
		}
		encrypted := must(q.server.Config.Encrypt(tokenPrefix + string(must(json.Marshal(tokens)))))
		row = q.update("communication_sources", row["id"], M{"credential_encrypted": encrypted, "enabled": ticket["enable_source"], "last_error": nil})
		return q.sourceRead(row, false)
	})
	s.mux.HandleFunc("GET /api/v1/admin/mts-link/extension.zip", func(w http.ResponseWriter, r *http.Request) {
		file := filepath.Join(s.Config.WebDir, "downloads", "ai-secretary-extension.zip")
		if _, e := os.Stat(file); e != nil {
			writeJSON(w, 503, M{"detail": "Архив расширения ещё не собран."})
			return
		}
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Disposition", `attachment; filename="ai-secretary-extension.zip"`)
		http.ServeFile(w, r, file)
	})
}
