package tailnet

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sync"

	"tailscale.com/ipn"
)

// stateStore keeps the node's state in memory for the app to save in its secure storage; Version counts the writes
// that changed something, so the app knows when to save again.
type stateStore struct {
	mu      sync.Mutex
	data    map[ipn.StateKey][]byte
	version int64
}

// loadStore restores a store from Export() output ("" = new node).
func loadStore(saved string) (*stateStore, error) {
	s := &stateStore{data: map[ipn.StateKey][]byte{}}
	if saved == "" {
		return s, nil
	}
	var m map[string][]byte
	if err := json.Unmarshal([]byte(saved), &m); err != nil {
		return nil, fmt.Errorf("%w: %v", errBadState, err)
	}
	for k, v := range m {
		s.data[ipn.StateKey(k)] = v
	}
	return s, nil
}

func (s *stateStore) String() string { return "tailnet.stateStore" }

// ReadState implements ipn.StateStore.
func (s *stateStore) ReadState(id ipn.StateKey) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	v, ok := s.data[id]
	if !ok {
		return nil, ipn.ErrStateNotExist
	}
	return bytes.Clone(v), nil
}

// WriteState implements ipn.StateStore.
func (s *stateStore) WriteState(id ipn.StateKey, v []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	old, ok := s.data[id]
	if v == nil {
		if !ok {
			return nil
		}
		delete(s.data, id)
	} else {
		if ok && bytes.Equal(old, v) {
			return nil
		}
		s.data[id] = bytes.Clone(v)
	}
	s.version++
	return nil
}

// Version is the number of changing writes since the store was loaded.
func (s *stateStore) Version() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.version
}

// Export is the whole state as JSON ({"key": base64}).
func (s *stateStore) Export() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	m := make(map[string][]byte, len(s.data))
	for k, v := range s.data {
		m[string(k)] = v
	}
	out, _ := json.Marshal(m)
	return string(out)
}
