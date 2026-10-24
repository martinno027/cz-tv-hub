// Package tailnet puts a TV app into the owner's Tailscale tailnet without the Tailscale app or a VPN profile
// (spec/apps.md "Tailscale v aplikaci"): a userspace tsnet node plus a bridge on 127.0.0.1 that forwards plain
// HTTP to the gateway over the tailnet. The app uses http://127.0.0.1:<ProxyPort()> as the gateway address.
//
// The node's state (its keys) is not written to disk: the app passes the last saved state to Start and stores
// SavedState() in its secure storage (Keychain / encrypted prefs) whenever Info() reports a new stateVersion.
// Tailscale's log upload is compiled out (ts_omit_logtail) and disabled at run time.
package tailnet

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"

	"tailscale.com/logtail"
	"tailscale.com/tsnet"
)

// Node is the app's place in the tailnet.
type Node struct {
	srv      *tsnet.Server
	store    *stateStore
	listener net.Listener
	httpSrv  *http.Server

	mu       sync.Mutex
	loginURL string
}

// Start brings the node up and opens the bridge to target ("raspberrypi:8098", "100.101.102.103:8098").
// workDir holds only Tailscale's caches; savedState is the last SavedState() (empty on first use); authKey may be
// empty, then Info().loginURL offers an interactive login.
func Start(workDir, savedState, hostname, authKey, target string) (*Node, error) {
	logtail.Disable()
	if _, _, err := net.SplitHostPort(target); err != nil {
		return nil, fmt.Errorf("gateway address %q: %w", target, err)
	}
	store, err := loadStore(savedState)
	if err != nil {
		return nil, err
	}
	n := &Node{store: store}
	n.srv = &tsnet.Server{
		Dir:      workDir,
		Store:    store,
		Hostname: hostname,
		AuthKey:  strings.TrimSpace(authKey),
		Logf:     func(string, ...any) {},
		UserLogf: n.userLog,
	}
	if err := n.srv.Start(); err != nil {
		return nil, err
	}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		n.srv.Close()
		return nil, err
	}
	n.listener = ln
	n.httpSrv = &http.Server{Handler: newBridge(target, n.srv.Dial), ReadHeaderTimeout: 10 * time.Second}
	go n.httpSrv.Serve(ln)
	return n, nil
}

// newBridge forwards every request to target through dial, as if the app had asked the gateway directly.
func newBridge(target string, dial func(ctx context.Context, network, addr string) (net.Conn, error)) http.Handler {
	upstream := &url.URL{Scheme: "http", Host: target}
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	director := proxy.Director
	proxy.Director = func(r *http.Request) {
		director(r)
		r.Host = target
	}
	proxy.FlushInterval = -1 // playlists, segments and JSON pass straight through
	proxy.Transport = &http.Transport{
		DialContext:           dial,
		ResponseHeaderTimeout: 60 * time.Second,
		IdleConnTimeout:       90 * time.Second,
		MaxIdleConnsPerHost:   8,
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(http.StatusBadGateway)
		body, _ := json.Marshal(map[string]string{"error": "Brána není přes Tailscale dostupná."})
		w.Write(body)
	}
	return proxy
}

func (n *Node) userLog(format string, args ...any) {
	if u := loginURLIn(fmt.Sprintf(format, args...)); u != "" {
		n.mu.Lock()
		n.loginURL = u
		n.mu.Unlock()
	}
}

// loginURLIn finds the login URL tsnet prints ("To authenticate, visit:\n\n\thttps://login.tailscale.com/a/…").
func loginURLIn(text string) string {
	i := strings.Index(text, "https://")
	if i < 0 {
		return ""
	}
	if f := strings.Fields(text[i:]); len(f) > 0 {
		return f[0]
	}
	return ""
}

// ProxyPort is the bridge's port on 127.0.0.1.
func (n *Node) ProxyPort() int {
	return n.listener.Addr().(*net.TCPAddr).Port
}

// Info is the node's state as JSON for the app's settings screen:
// {"state": "NoState|NeedsLogin|NeedsMachineAuth|Starting|Running|Stopped", "loginURL": "…", "self": "name",
// "ip": "100.…", "stateVersion": 3}.
func (n *Node) Info() string {
	info := struct {
		State        string `json:"state"`
		LoginURL     string `json:"loginURL"`
		Self         string `json:"self"`
		IP           string `json:"ip"`
		StateVersion int64  `json:"stateVersion"`
	}{State: "NoState", StateVersion: n.store.Version()}
	if lc, err := n.srv.LocalClient(); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if st, err := lc.StatusWithoutPeers(ctx); err == nil {
			info.State = st.BackendState
			if st.AuthURL != "" {
				n.mu.Lock()
				n.loginURL = st.AuthURL
				n.mu.Unlock()
			}
			if st.Self != nil {
				info.Self = st.Self.HostName
				if len(st.Self.TailscaleIPs) > 0 {
					info.IP = st.Self.TailscaleIPs[0].String()
				}
			}
		}
	}
	if info.State == "NeedsLogin" {
		n.mu.Lock()
		info.LoginURL = n.loginURL
		n.mu.Unlock()
	}
	out, _ := json.Marshal(info)
	return string(out)
}

// SavedState is the node's state to keep in the app's secure storage (JSON, contains the node's private keys).
func (n *Node) SavedState() string {
	return n.store.Export()
}

// Logout signs the node out of the tailnet; the app then deletes its saved state.
func (n *Node) Logout() error {
	lc, err := n.srv.LocalClient()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return lc.Logout(ctx)
}

// Close stops the bridge and the node.
func (n *Node) Close() {
	if n.httpSrv != nil {
		n.httpSrv.Close()
	}
	if n.srv != nil {
		n.srv.Close()
	}
}


var errBadState = errors.New("saved Tailscale state is not valid JSON")
