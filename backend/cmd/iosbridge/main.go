// Build as a c-archive; the ABI returns owned UTF-8 JSON and exposes no Go pointers.
package main

/*
#include <stdlib.h>
*/
import "C"
import (
	"context"
	"encoding/json"
	"errors"
	"github.com/mumg/ai_secretary/backend/mobileclient"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"
)

var clients sync.Map
var serial atomic.Int64
var requests sync.Map

type pendingRequest struct {
	owner  int64
	ctx    context.Context
	cancel context.CancelFunc
}

//export SecretaryCall
func SecretaryCall(input *C.char) *C.char {
	data, err := call([]byte(C.GoString(input)))
	result := map[string]any{"data": data}
	if err != nil {
		result = map[string]any{"error": err.Error()}
	}
	b, _ := json.Marshal(result)
	return C.CString(string(b))
}

//export SecretaryFree
func SecretaryFree(p *C.char) { C.free(unsafe.Pointer(p)) }
func call(raw []byte) (any, error) {
	var q struct {
		RequestID int64           `json:"request_id"`
		Op        string          `json:"op"`
		Handle    int64           `json:"handle"`
		Server    string          `json:"server"`
		QR        string          `json:"qr"`
		Method    string          `json:"method"`
		Path      string          `json:"path"`
		Body      json.RawMessage `json:"body"`
	}
	if err := json.Unmarshal(raw, &q); err != nil {
		return nil, errors.New("Некорректный запрос")
	}
	if q.Op == "open" {
		c, err := mobileclient.New(q.Server, q.QR)
		if err != nil {
			return nil, err
		}
		id := serial.Add(1)
		clients.Store(id, c)
		return map[string]any{"handle": id, "server": c.Identity.Server, "gateway": c.Identity.Installation != ""}, nil
	}
	if q.Op == "cancel" {
		if v, ok := requests.Load(q.RequestID); ok {
			v.(*pendingRequest).cancel()
		}
		return true, nil
	}
	value, ok := clients.Load(q.Handle)
	if !ok {
		return nil, errors.New("Подключение закрыто")
	}
	c := value.(*mobileclient.Client)
	switch q.Op {
	case "close":
		clients.Delete(q.Handle)
		c.Close()
		return true, nil
	case "push":
		var push struct {
			DeviceID string `json:"device_id"`
			Key      string `json:"key"`
			Revision int64  `json:"revision"`
			Token    string `json:"token"`
		}
		if err := json.Unmarshal(q.Body, &push); err != nil {
			return nil, err
		}
		return true, c.RegisterPush(context.Background(), push.DeviceID, push.Key, push.Revision, push.Token)
	case "prepare":
		ctx, cancel := context.WithTimeout(context.Background(), 50*time.Second)
		id := serial.Add(1)
		requests.Store(id, &pendingRequest{q.Handle, ctx, cancel})
		time.AfterFunc(55*time.Second, func() {
			if v, ok := requests.LoadAndDelete(id); ok {
				v.(*pendingRequest).cancel()
			}
		})
		return id, nil
	case "request":
		v, ok := requests.Load(q.RequestID)
		if !ok || v.(*pendingRequest).owner != q.Handle {
			return nil, errors.New("Запрос отменён")
		}
		pending := v.(*pendingRequest)
		defer requests.Delete(q.RequestID)
		defer pending.cancel()
		body := q.Body
		if string(body) == "null" {
			body = nil
		}
		return c.Request(pending.ctx, q.Method, q.Path, body)
	case "watch":
		c.StartRealtime()
		return true, nil
	case "event":
		ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
		defer cancel()
		return c.NextEvent(ctx), nil
	default:
		return nil, errors.New("Неизвестная операция")
	}
}
func main() {}
