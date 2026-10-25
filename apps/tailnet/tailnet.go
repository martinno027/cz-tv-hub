// Package tailnet puts a TV app into the owner's Tailscale tailnet without the Tailscale app or a VPN profile
// (spec/apps.md "Tailscale v aplikaci"): a userspace tsnet node plus a bridge on 127.0.0.1 that forwards plain
// HTTP to the gateway over the tailnet. The app uses http://127.0.0.1:<ProxyPort()> as the gateway address.
//
// The node's state (its keys) is not written to disk: the app passes the last saved state to Start and stores
// SavedState() in its secure storage (Keychain / encrypted prefs) whenever Info() reports a new stateVersion.
// Tailscale's log upload is compiled out (ts_omit_logtail) and disabled at run time.
//
// Back in front (spec/apps.md, T18): a suspended app's node can lose its connections while it still says Running;
// the app then calls Restart, which starts the node again with its in-memory state on the same bridge port.
package tailnet

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net"
	"net/http"
	"net/http/httputil"
	"net/netip"
	"net/url"
	"strings"
	"sync"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/logtail"
	"tailscale.com/tailcfg"
	"tailscale.com/tsnet"
)

// bridgeError is the bridge's own 502 text; the apps tell it from the gateway's answers by it (spec/apps.md T18).
const bridgeError = "Brána není přes Tailscale dostupná."

// dialTimeout stays under the apps' 10 s request timeout, so an unreachable gateway answers the bridge's 502
// (which makes the app check the bridge) instead of the app giving up on its own.
const dialTimeout = 8 * time.Second

// pingTimeout bounds the background ping behind Info().gateway.latencyMs.
const pingTimeout = 2 * time.Second

// Node is the app's place in the tailnet.
type Node struct {
	store      *stateStore
	listener   net.Listener
	httpSrv    *http.Server
	transport  *http.Transport
	targetHost string
	workDir    string
	hostname   string
	authKey    string
	controlURL string // tests only: a coordination server that does not exist

	mu       sync.Mutex
	srv      *tsnet.Server // nil while Restart is between the old and the new node
	loginURL string
	probe    pathProbe
}

// Start brings the node up and opens the bridge to target ("raspberrypi:8098", "100.101.102.103:8098").
// workDir holds only Tailscale's caches; savedState is the last SavedState() (empty on first use); authKey may be
// empty, then Info().loginURL offers an interactive login.
func Start(workDir, savedState, hostname, authKey, target string) (*Node, error) {
	return start(workDir, savedState, hostname, authKey, target, "")
}

func start(workDir, savedState, hostname, authKey, target, controlURL string) (*Node, error) {
	logtail.Disable()
	host, _, err := net.SplitHostPort(target)
	if err != nil {
		return nil, fmt.Errorf("gateway address %q: %w", target, err)
	}
	store, err := loadStore(savedState)
	if err != nil {
		return nil, err
	}
	n := &Node{store: store, targetHost: host, workDir: workDir, hostname: hostname,
		authKey: strings.TrimSpace(authKey), controlURL: controlURL}
	srv := n.newServer()
	if err := srv.Start(); err != nil {
		return nil, err
	}
	n.srv = srv
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		srv.Close()
		return nil, err
	}
	n.listener = ln
	proxy := newBridge(target, n.dial)
	n.transport = proxy.Transport.(*http.Transport)
	n.httpSrv = &http.Server{Handler: proxy, ReadHeaderTimeout: 10 * time.Second}
	go n.httpSrv.Serve(ln)
	return n, nil
}

func (n *Node) newServer() *tsnet.Server {
	return &tsnet.Server{
		Dir:        n.workDir,
		Store:      n.store,
		Hostname:   n.hostname,
		AuthKey:    n.authKey,
		ControlURL: n.controlURL,
		Logf:       func(string, ...any) {},
		UserLogf:   n.userLog,
	}
}

func (n *Node) server() *tsnet.Server {
	n.mu.Lock()
	defer n.mu.Unlock()
	return n.srv
}

// dial goes through the node running now (Restart replaces it under the bridge).
func (n *Node) dial(ctx context.Context, network, addr string) (net.Conn, error) {
	srv := n.server()
	if srv == nil {
		return nil, errors.New("tailnet node is restarting")
	}
	return srv.Dial(ctx, network, addr)
}

// Restart stops the node and starts it again with its current state (no new login, the same identity), keeping
// the bridge and its port, so the app's gateway address and a player's URLs stay valid. The bridge's pooled
// connections to the gateway belonged to the old node and are dropped.
func (n *Node) Restart() error {
	n.mu.Lock()
	old := n.srv
	n.srv = nil
	n.mu.Unlock()
	if old != nil {
		old.Close()
	}
	n.transport.CloseIdleConnections()
	n.probe.forget()
	srv := n.newServer()
	if err := srv.Start(); err != nil {
		return err
	}
	n.mu.Lock()
	n.srv = srv
	n.mu.Unlock()
	return nil
}

// newBridge forwards every request to target through dial, as if the app had asked the gateway directly. Its
// transport keeps connections to the gateway open between requests (measured: 1 dial for 150 requests).
func newBridge(target string, dial func(ctx context.Context, network, addr string) (net.Conn, error)) *httputil.ReverseProxy {
	upstream := &url.URL{Scheme: "http", Host: target}
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	director := proxy.Director
	proxy.Director = func(r *http.Request) {
		director(r)
		r.Host = target
	}
	proxy.FlushInterval = -1 // playlists, segments and JSON pass straight through
	proxy.Transport = &http.Transport{
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			ctx, cancel := context.WithTimeout(ctx, dialTimeout)
			defer cancel()
			return dial(ctx, network, addr)
		},
		ResponseHeaderTimeout: 60 * time.Second,
		IdleConnTimeout:       90 * time.Second,
		MaxIdleConnsPerHost:   8,
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		w.WriteHeader(http.StatusBadGateway)
		body, _ := json.Marshal(map[string]string{"error": bridgeError})
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

// nodeInfo is Info()'s JSON (spec/fixtures/tailnet_cases.json "status").
type nodeInfo struct {
	State        string       `json:"state"`
	LoginURL     string       `json:"loginURL"`
	Self         string       `json:"self"`
	IP           string       `json:"ip"`
	StateVersion int64        `json:"stateVersion"`
	Gateway      *gatewayPath `json:"gateway"`
}

// gatewayPath is how the node reaches the gateway's peer: directly (UDP) or through a DERP relay, and the last
// ping's round trip (nil until one answered).
type gatewayPath struct {
	Direct    bool   `json:"direct"`
	Relay     string `json:"relay"`
	LatencyMs *int   `json:"latencyMs"`
}

// Info is the node's state as JSON for the app's settings screen:
// {"state": "NoState|NeedsLogin|NeedsMachineAuth|Starting|Running|Stopped", "loginURL": "…", "self": "name",
// "ip": "100.…", "stateVersion": 3, "gateway": {"direct": false, "relay": "fra", "latencyMs": 85} | null}.
func (n *Node) Info() string {
	info := nodeInfo{State: "NoState", StateVersion: n.store.Version()}
	srv := n.server()
	if srv == nil {
		info.State = "Starting"
	} else if lc, err := srv.LocalClient(); err == nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if st, err := lc.Status(ctx); err == nil {
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
			if peer := findPeer(st, n.targetHost); info.State == "Running" && peer != nil {
				ip := peer.TailscaleIPs[0]
				info.Gateway = &gatewayPath{
					Direct: peer.CurAddr != "",
					Relay:  peer.Relay,
					LatencyMs: n.probe.latency(ip, func(ctx context.Context) (time.Duration, error) {
						res, err := lc.Ping(ctx, ip, tailcfg.PingDisco)
						if err == nil && res.Err != "" {
							err = errors.New(res.Err)
						}
						if err != nil {
							return 0, err
						}
						return time.Duration(res.LatencySeconds * float64(time.Second)), nil
					}),
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

// findPeer is the peer the bridge's target host names: a Tailscale IP, the MagicDNS name (full or its first
// label) or the peer's host name; nil when none matches (a LAN address, a subnet route, an unknown name).
func findPeer(st *ipnstate.Status, host string) *ipnstate.PeerStatus {
	if ip, err := netip.ParseAddr(strings.Trim(host, "[]")); err == nil {
		for _, p := range st.Peer {
			for _, a := range p.TailscaleIPs {
				if a == ip {
					return p
				}
			}
		}
		return nil
	}
	name := strings.ToLower(strings.TrimSuffix(host, "."))
	matches := []func(p *ipnstate.PeerStatus) bool{
		func(p *ipnstate.PeerStatus) bool { return strings.ToLower(strings.TrimSuffix(p.DNSName, ".")) == name },
		func(p *ipnstate.PeerStatus) bool {
			label, _, _ := strings.Cut(strings.ToLower(p.DNSName), ".")
			return label == name
		},
		func(p *ipnstate.PeerStatus) bool { return strings.EqualFold(p.HostName, name) },
	}
	for _, match := range matches {
		for _, p := range st.Peer {
			if len(p.TailscaleIPs) > 0 && match(p) {
				return p
			}
		}
	}
	return nil
}

// pathProbe pings the gateway's peer in the background, one ping at a time, so Info stays fast: every Info call
// starts a new ping once the previous one has finished and returns the last answer.
type pathProbe struct {
	mu      sync.Mutex
	ip      netip.Addr
	ms      *int
	running bool
	round   int // bumped by forget: a ping of the old node does not count
}

func (p *pathProbe) latency(ip netip.Addr, ping func(ctx context.Context) (time.Duration, error)) *int {
	p.mu.Lock()
	defer p.mu.Unlock()
	if ip != p.ip {
		p.ip = ip
		p.ms = nil
		p.round++
	}
	if !p.running {
		p.running = true
		round := p.round
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), pingTimeout)
			d, err := ping(ctx)
			cancel()
			p.mu.Lock()
			defer p.mu.Unlock()
			p.running = false
			if round != p.round {
				return
			}
			if err != nil {
				p.ms = nil
			} else {
				ms := int(math.Round(float64(d) / float64(time.Millisecond)))
				p.ms = &ms
			}
		}()
	}
	if p.ms == nil {
		return nil
	}
	ms := *p.ms
	return &ms
}

// forget drops the last answer (a restarted node has a new path).
func (p *pathProbe) forget() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.ms = nil
	p.round++
}

// SavedState is the node's state to keep in the app's secure storage (JSON, contains the node's private keys).
func (n *Node) SavedState() string {
	return n.store.Export()
}

// Logout signs the node out of the tailnet; the app then deletes its saved state.
func (n *Node) Logout() error {
	srv := n.server()
	if srv == nil {
		return errors.New("tailnet node is restarting")
	}
	lc, err := srv.LocalClient()
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
	if srv := n.server(); srv != nil {
		srv.Close()
	}
}

var errBadState = errors.New("saved Tailscale state is not valid JSON")
