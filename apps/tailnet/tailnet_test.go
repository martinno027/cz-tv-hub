package tailnet

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/ipn"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/types/key"
)

func TestStoreRoundTripAndVersion(t *testing.T) {
	s, err := loadStore("")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.ReadState("_machinekey"); err != ipn.ErrStateNotExist {
		t.Fatalf("empty store: %v", err)
	}
	s.WriteState("_machinekey", []byte("secret"))
	s.WriteState("_machinekey", []byte("secret")) // unchanged: no new version
	s.WriteState("profile", []byte{0, 1, 2})
	if got := s.Version(); got != 2 {
		t.Fatalf("version = %d, want 2", got)
	}
	again, err := loadStore(s.Export())
	if err != nil {
		t.Fatal(err)
	}
	if v, _ := again.ReadState("profile"); string(v) != "\x00\x01\x02" {
		t.Fatalf("restored profile = %q", v)
	}
	if again.Version() != 0 {
		t.Fatal("a restored store starts at version 0")
	}
	again.WriteState("profile", nil)
	if _, err := again.ReadState("profile"); err != ipn.ErrStateNotExist || again.Version() != 1 {
		t.Fatalf("delete: %v, version %d", err, again.Version())
	}
	if _, err := loadStore("{not json"); err == nil {
		t.Fatal("bad saved state must be refused")
	}
}

func TestLoginURLIn(t *testing.T) {
	cases := map[string]string{
		"\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/abc123\n\n": "https://login.tailscale.com/a/abc123",
		"Success.": "",
	}
	for in, want := range cases {
		if got := loginURLIn(in); got != want {
			t.Errorf("loginURLIn(%q) = %q, want %q", in, got, want)
		}
	}
}

// The bridge forwards method, path, query, headers and body to the gateway and streams the answer back.
func TestBridgeForwardsToGateway(t *testing.T) {
	var seen *http.Request
	var body string
	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		seen = r
		b, _ := io.ReadAll(r.Body)
		body = string(b)
		w.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
		io.WriteString(w, "#EXTM3U\n/dvb/v/abc/v.m3u8\n")
	}))
	defer gw.Close()
	var dialed string
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		dialed = addr
		return (&net.Dialer{}).DialContext(ctx, network, gw.Listener.Addr().String())
	}
	bridge := httptest.NewServer(newBridge("raspberrypi:8098", dial))
	defer bridge.Close()

	req, _ := http.NewRequest("PUT", bridge.URL+"/dvb/profile?x=1", strings.NewReader(`{"v":1}`))
	req.Header.Set("Authorization", "Bearer k")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	out, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if dialed != "raspberrypi:8098" {
		t.Errorf("dialed %q", dialed)
	}
	if seen.Method != "PUT" || seen.URL.Path != "/dvb/profile" || seen.URL.RawQuery != "x=1" || seen.Host != "raspberrypi:8098" {
		t.Errorf("gateway saw %s %s?%s host %s", seen.Method, seen.URL.Path, seen.URL.RawQuery, seen.Host)
	}
	if seen.Header.Get("Authorization") != "Bearer k" || body != `{"v":1}` {
		t.Errorf("auth %q body %q", seen.Header.Get("Authorization"), body)
	}
	if resp.StatusCode != 200 || string(out) != "#EXTM3U\n/dvb/v/abc/v.m3u8\n" {
		t.Errorf("answer %d %q", resp.StatusCode, out)
	}
}

func TestBridgeUnreachableGatewayIsCzechJSON(t *testing.T) {
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		return nil, &net.OpError{Op: "dial", Err: io.EOF}
	}
	bridge := httptest.NewServer(newBridge("raspberrypi:8098", dial))
	defer bridge.Close()
	resp, err := http.Get(bridge.URL + "/dvb/channels")
	if err != nil {
		t.Fatal(err)
	}
	out, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 502 || !strings.Contains(string(out), "Brána není přes Tailscale dostupná.") {
		t.Errorf("%d %s", resp.StatusCode, out)
	}
}

// A new tsnet dial per request costs a round trip over the tailnet each time (a live start is a playlist and
// several segments): the bridge must keep its connection to the gateway.
func TestBridgeReusesTheGatewayConnection(t *testing.T) {
	gw := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "ok\n")
	}))
	defer gw.Close()
	var dials int64
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		atomic.AddInt64(&dials, 1)
		return (&net.Dialer{}).DialContext(ctx, network, gw.Listener.Addr().String())
	}
	bridge := httptest.NewServer(newBridge("raspberrypi:8098", dial))
	defer bridge.Close()
	// AVPlayer and URLSession may open new connections to the bridge; the bridge's side must still be reused.
	client := &http.Client{Transport: &http.Transport{DisableKeepAlives: true}}
	for i := 0; i < 5; i++ {
		resp, err := client.Get(bridge.URL + "/health")
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
	}
	if got := atomic.LoadInt64(&dials); got != 1 {
		t.Errorf("%d dials for 5 requests, want 1", got)
	}
}

// A dial that hangs (a peer gone while the node says Running) gives the bridge's 502 before the apps' own 10 s
// timeout, so they check the bridge.
func TestBridgeDialGivesUpBeforeTheApps(t *testing.T) {
	if dialTimeout >= 10*time.Second {
		t.Fatalf("dialTimeout %v is not under the apps' 10 s request timeout", dialTimeout)
	}
	var deadline time.Duration
	dial := func(ctx context.Context, network, addr string) (net.Conn, error) {
		d, _ := ctx.Deadline()
		deadline = time.Until(d)
		return nil, context.DeadlineExceeded
	}
	bridge := httptest.NewServer(newBridge("raspberrypi:8098", dial))
	defer bridge.Close()
	resp, err := http.Get(bridge.URL + "/dvb/channels")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 502 || deadline <= 0 || deadline > dialTimeout {
		t.Errorf("status %d, dial deadline in %v", resp.StatusCode, deadline)
	}
}

func peer(dnsName, hostName, ip string) *ipnstate.PeerStatus {
	return &ipnstate.PeerStatus{DNSName: dnsName, HostName: hostName, TailscaleIPs: []netip.Addr{netip.MustParseAddr(ip)}}
}

func TestFindPeerOfTheBridgeTarget(t *testing.T) {
	pi := peer("raspberrypi.tail1234.ts.net.", "raspberrypi", "100.101.102.103")
	phone := peer("phone.tail1234.ts.net.", "Pixel", "100.64.0.9")
	renamed := peer("gateway.tail1234.ts.net.", "pi-at-home", "100.64.0.10")
	st := &ipnstate.Status{Peer: map[key.NodePublic]*ipnstate.PeerStatus{
		key.NewNode().Public(): pi, key.NewNode().Public(): phone, key.NewNode().Public(): renamed,
	}}
	cases := map[string]*ipnstate.PeerStatus{
		"raspberrypi":                  pi,
		"RaspberryPi":                  pi,
		"raspberrypi.tail1234.ts.net":  pi,
		"raspberrypi.tail1234.ts.net.": pi,
		"100.101.102.103":              pi,
		"gateway":                      renamed,
		"pi-at-home":                   renamed,
		"192.168.1.20":                 nil,
		"100.64.0.99":                  nil,
		"tail1234":                     nil,
		"other.example.org":            nil,
	}
	for host, want := range cases {
		if got := findPeer(st, host); got != want {
			t.Errorf("findPeer(%q) = %v, want %v", host, got, want)
		}
	}
}

// Info's JSON is the shared contract: every "status" golden case's info decodes and encodes back unchanged.
func TestInfoJSONMatchesTheGoldenCases(t *testing.T) {
	raw, err := os.ReadFile("../../spec/fixtures/tailnet_cases.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Status []struct {
			Name string          `json:"name"`
			Info json.RawMessage `json:"info"`
		} `json:"status"`
	}
	if err := json.Unmarshal(raw, &fixture); err != nil {
		t.Fatal(err)
	}
	withGateway := 0
	for _, c := range fixture.Status {
		if string(c.Info) == "null" {
			continue
		}
		var want map[string]any
		json.Unmarshal(c.Info, &want)
		if _, ok := want["gateway"]; !ok {
			continue // cases from before T18
		}
		withGateway++
		var info nodeInfo
		if err := json.Unmarshal(c.Info, &info); err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		out, _ := json.Marshal(info)
		var got map[string]any
		json.Unmarshal(out, &got)
		gotJSON, _ := json.Marshal(got)
		wantJSON, _ := json.Marshal(want)
		if string(gotJSON) != string(wantJSON) {
			t.Errorf("%s:\n got %s\nwant %s", c.Name, gotJSON, wantJSON)
		}
	}
	if withGateway != 4 {
		t.Fatalf("%d golden cases with a gateway path, want 4", withGateway)
	}
}

// The ping runs in the background (Info stays fast), one at a time; a failed ping or another peer clears it.
func TestPathProbe(t *testing.T) {
	var p pathProbe
	pi := netip.MustParseAddr("100.101.102.103")
	release := make(chan struct{})
	var pings int64
	ping := func(result time.Duration, err error) func(context.Context) (time.Duration, error) {
		return func(ctx context.Context) (time.Duration, error) {
			atomic.AddInt64(&pings, 1)
			if _, ok := ctx.Deadline(); !ok {
				t.Error("a ping without a deadline")
			}
			<-release
			return result, err
		}
	}
	waitIdle := func() {
		for i := 0; i < 200; i++ {
			p.mu.Lock()
			running := p.running
			p.mu.Unlock()
			if !running {
				return
			}
			time.Sleep(5 * time.Millisecond)
		}
		t.Fatal("ping still running")
	}
	if got := p.latency(pi, ping(85*time.Millisecond+400*time.Microsecond, nil)); got != nil {
		t.Fatalf("before any answer: %v", *got)
	}
	p.latency(pi, ping(time.Second, nil)) // still running: no second ping
	release <- struct{}{}
	waitIdle()
	if got := atomic.LoadInt64(&pings); got != 1 {
		t.Fatalf("%d pings, want 1 at a time", got)
	}
	if got := p.latency(pi, ping(0, errors.New("timeout"))); got == nil || *got != 85 {
		t.Fatalf("after the answer: %v", got)
	}
	release <- struct{}{}
	waitIdle()
	if got := p.latency(pi, ping(32*time.Millisecond, nil)); got != nil {
		t.Fatalf("after a failed ping: %v", *got)
	}
	release <- struct{}{}
	waitIdle()
	if got := p.latency(netip.MustParseAddr("100.64.0.10"), ping(0, nil)); got != nil {
		t.Fatalf("another peer must not show the old latency: %v", *got)
	}
	release <- struct{}{}
	waitIdle()
	p.forget()
	if got := p.latency(netip.MustParseAddr("100.64.0.10"), ping(0, nil)); got != nil {
		t.Fatalf("after forget: %v", *got)
	}
	release <- struct{}{}
	waitIdle()
}

// Restart keeps the bridge's port (the app's gateway address and a player's URLs stay valid) and the node's state.
// The node talks to a coordination server that does not exist: no tailnet, no login.
func TestRestartKeepsTheBridgePort(t *testing.T) {
	closed, _ := net.Listen("tcp", "127.0.0.1:0")
	control := "http://" + closed.Addr().String()
	closed.Close()
	n, err := start(t.TempDir(), "", "televize-test", "", "raspberrypi:8098", control)
	if err != nil {
		t.Fatal(err)
	}
	defer n.Close()
	port := n.ProxyPort()
	if err := n.Restart(); err != nil {
		t.Fatal(err)
	}
	if n.ProxyPort() != port {
		t.Fatalf("port %d after restart, was %d", n.ProxyPort(), port)
	}
	var info nodeInfo
	if err := json.Unmarshal([]byte(n.Info()), &info); err != nil || info.State == "" || info.Gateway != nil {
		t.Fatalf("info after restart: %+v %v", info, err)
	}
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Get(n.listenerURL() + "/health")
	if err != nil {
		t.Fatal(err)
	}
	out, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 502 || !strings.Contains(string(out), bridgeError) {
		t.Errorf("bridge after restart: %d %s", resp.StatusCode, out)
	}
}

func (n *Node) listenerURL() string { return "http://" + n.listener.Addr().String() }

func TestStartRefusesAddressWithoutPort(t *testing.T) {
	if _, err := Start(t.TempDir(), "", "televize-test", "", "raspberrypi"); err == nil {
		t.Fatal("an address without a port must be refused before the node starts")
	}
}

func TestQRMatrixIsSquareWithFinderPatterns(t *testing.T) {
	m, err := QRMatrix("https://login.tailscale.com/a/1a2b3c4d5e6f7")
	if err != nil {
		t.Fatal(err)
	}
	rows := strings.Split(m, "\n")
	for _, r := range rows {
		if len(r) != len(rows) || strings.Trim(r, "01") != "" {
			t.Fatalf("row %q in a %d-row matrix", r, len(rows))
		}
	}
	// quiet zone of 4, then the top-left finder pattern's first row: 7 dark modules
	if rows[0] != strings.Repeat("0", len(rows)) || rows[4][4:11] != "1111111" || rows[4][3] != '0' {
		t.Fatalf("no quiet zone / finder pattern:\n%s", m)
	}
	other, _ := QRMatrix("https://login.tailscale.com/a/ffffffffffff0")
	if other == m {
		t.Fatal("different URLs must give different codes")
	}
}

// The apps' gateway clients wait 10 s (Android GATEWAY_TIMEOUT_MS, tvOS URLSession): the bridge must give up
// dialling first, so the app sees the bridge's 502 and starts the recovery (spec/apps.md "Back in front (T18)").
func TestDialTimeoutStaysBelowTheAppsTimeout(t *testing.T) {
	if dialTimeout != 8*time.Second {
		t.Fatalf("dialTimeout = %v; the apps mirror 8 s (BridgeWatch.BRIDGE_DIAL_TIMEOUT_MS) and wait 10 s", dialTimeout)
	}
}
