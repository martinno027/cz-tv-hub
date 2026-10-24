package tailnet

import (
	"context"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"tailscale.com/ipn"
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
