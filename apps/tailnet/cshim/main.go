// Command cshim exports package tailnet as a C library for the tvOS app. Go has no tvOS target: build.py builds it
// with GOOS=ios against the appletvos/appletvsimulator SDKs. Returned strings are malloc'ed; the caller frees them.
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"sync"

	"cztvhub.local/tailnet"
)

var (
	mu      sync.Mutex
	node    *tailnet.Node
	lastErr string
)

// TailnetStart starts the node (stopping a running one) and returns the bridge's port on 127.0.0.1, or -1 with
// TailnetLastError set.
//
//export TailnetStart
func TailnetStart(workDir, savedState, hostname, authKey, target *C.char) C.int {
	mu.Lock()
	defer mu.Unlock()
	if node != nil {
		node.Close()
		node = nil
	}
	n, err := tailnet.Start(C.GoString(workDir), C.GoString(savedState), C.GoString(hostname),
		C.GoString(authKey), C.GoString(target))
	if err != nil {
		lastErr = err.Error()
		return -1
	}
	lastErr = ""
	node = n
	return C.int(n.ProxyPort())
}

//export TailnetLastError
func TailnetLastError() *C.char {
	mu.Lock()
	defer mu.Unlock()
	return C.CString(lastErr)
}

// TailnetInfo is Node.Info(), or {"state":"Stopped"} without a node.
//
//export TailnetInfo
func TailnetInfo() *C.char {
	mu.Lock()
	defer mu.Unlock()
	if node == nil {
		return C.CString(`{"state":"Stopped"}`)
	}
	return C.CString(node.Info())
}

//export TailnetSavedState
func TailnetSavedState() *C.char {
	mu.Lock()
	defer mu.Unlock()
	if node == nil {
		return C.CString("")
	}
	return C.CString(node.SavedState())
}

// TailnetLogout returns 0 on success, -1 with TailnetLastError set.
//
//export TailnetLogout
func TailnetLogout() C.int {
	mu.Lock()
	defer mu.Unlock()
	if node == nil {
		return 0
	}
	if err := node.Logout(); err != nil {
		lastErr = err.Error()
		return -1
	}
	return 0
}

// TailnetQR is tailnet.QRMatrix(text), "" on error.
//
//export TailnetQR
func TailnetQR(text *C.char) *C.char {
	m, err := tailnet.QRMatrix(C.GoString(text))
	if err != nil {
		return C.CString("")
	}
	return C.CString(m)
}

//export TailnetStop
func TailnetStop() {
	mu.Lock()
	defer mu.Unlock()
	if node != nil {
		node.Close()
		node = nil
	}
}

func main() {}
