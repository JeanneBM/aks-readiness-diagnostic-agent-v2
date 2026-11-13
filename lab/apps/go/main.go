// Minimal net/http server for the lab. Like most Go services built on net/http,
// it logs nothing unless it fails to start.
package main

import (
	"net/http"
	"os"
)

func main() {
	addr := os.Getenv("LISTEN_ADDR")
	if addr == "" {
		addr = ":8080"
	}
	http.HandleFunc("/ready", func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte("ok\n"))
	})
	if err := http.ListenAndServe(addr, nil); err != nil {
		panic(err)
	}
}
