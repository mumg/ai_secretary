package gatewaylink

import (
	"bufio"
	"bytes"
	"io"
	"net"
	"sync"
	"time"

	"github.com/hashicorp/yamux"
)

// The gateway relays this envelope unchanged. Every nested stream still starts
// with independently authenticated, pinned end-to-end TLS, just like legacy clients.
const mobileMuxPreface = "AI-SECRETARY-MUX/1\n"

type mobileListener struct {
	session *yamux.Session
	ready   chan net.Conn
	done    chan struct{}
	once    sync.Once
}

func newMobileListener(session *yamux.Session) *mobileListener {
	l := &mobileListener{session: session, ready: make(chan net.Conn), done: make(chan struct{})}
	go l.run()
	return l
}
func (l *mobileListener) run() {
	defer l.Close()
	for {
		stream, err := l.session.AcceptStream()
		if err != nil {
			return
		}
		go l.acceptMobile(&tlsStream{stream})
	}
}
func (l *mobileListener) deliver(conn net.Conn) bool {
	select {
	case l.ready <- conn:
		return true
	case <-l.done:
		conn.Close()
		return false
	}
}
func (l *mobileListener) acceptMobile(conn net.Conn) {
	reader := bufio.NewReader(conn)
	conn.SetReadDeadline(time.Now().Add(15 * time.Second))
	first, err := reader.Peek(1)
	if err != nil {
		conn.Close()
		return
	}
	if first[0] == 0x16 { // Legacy TLS ClientHello.
		conn.SetReadDeadline(time.Time{})
		l.deliver(&bufferedConn{Conn: conn, reader: reader})
		return
	}
	preface := make([]byte, len(mobileMuxPreface))
	if _, err = io.ReadFull(reader, preface); err != nil || !bytes.Equal(preface, []byte(mobileMuxPreface)) {
		conn.Close()
		return
	}
	conn.SetReadDeadline(time.Time{})
	defer conn.Close()
	nested, err := yamux.Server(&bufferedConn{Conn: conn, reader: reader}, yamuxConfig())
	if err != nil {
		return
	}
	defer nested.Close()
	for {
		stream, err := nested.AcceptStream()
		if err != nil {
			return
		}
		if nested.NumStreams() > 32 {
			stream.Close()
			return
		}
		if !l.deliver(&tlsStream{stream}) {
			return
		}
	}
}
func (l *mobileListener) Accept() (net.Conn, error) {
	select {
	case conn := <-l.ready:
		return conn, nil
	case <-l.done:
		return nil, net.ErrClosed
	}
}
func (l *mobileListener) Close() error {
	l.once.Do(func() { close(l.done); l.session.Close() })
	return nil
}
func (l *mobileListener) Addr() net.Addr { return l.session.Addr() }

type bufferedConn struct {
	net.Conn
	reader *bufio.Reader
}

func (c *bufferedConn) Read(p []byte) (int, error) { return c.reader.Read(p) }
