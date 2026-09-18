package gatewaylink

import (
	"bytes"
	"io"
	"net"
	"sync"
	"testing"
	"time"

	"github.com/hashicorp/yamux"
)

func TestMobileListenerMultiplexAndLegacy(t *testing.T) {
	a, b := net.Pipe()
	client, _ := yamux.Client(a, yamuxConfig())
	server, _ := yamux.Server(b, yamuxConfig())
	listener := newMobileListener(server)
	defer client.Close()
	defer listener.Close()
	go func() {
		for {
			c, err := listener.Accept()
			if err != nil {
				return
			}
			go func() { defer c.Close(); io.Copy(c, c) }()
		}
	}()
	// Existing TLS clients are passed through without a protocol change.
	legacy, _ := client.OpenStream()
	legacy.SetDeadline(time.Now().Add(5 * time.Second))
	payload := []byte{0x16, 0x03, 0x03, 0, 1, 42}
	legacy.Write(payload)
	response := make([]byte, len(payload))
	if _, err := io.ReadFull(legacy, response); err != nil || !bytes.Equal(response, payload) {
		t.Fatalf("legacy: %x %v", response, err)
	}
	legacy.Close()
	// A new Android client opens one outer stream and several logical TLS streams.
	physical, _ := client.OpenStream()
	physical.Write([]byte(mobileMuxPreface))
	mux, err := yamux.Client(physical, yamuxConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer mux.Close()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			stream, err := mux.OpenStream()
			if err != nil {
				t.Error(err)
				return
			}
			defer stream.Close()
			stream.SetDeadline(time.Now().Add(10 * time.Second))
			data := bytes.Repeat([]byte{byte(i)}, 1<<20)
			sent := make(chan error, 1)
			go func() { _, err := stream.Write(data); sent <- err }()
			got := make([]byte, len(data))
			if _, err = io.ReadFull(stream, got); err != nil || !bytes.Equal(got, data) {
				t.Errorf("stream %d: %v", i, err)
			}
			if err = <-sent; err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	if client.NumStreams() != 1 {
		t.Fatalf("outer streams: %d", client.NumStreams())
	}
	mux.Close()
	listener.Close()
	if _, err = listener.Accept(); err == nil {
		t.Fatal("closed listener accepted a connection")
	}
}
