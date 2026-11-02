PLUGIN := navidrome-rpc
WASM := plugin.wasm
TINYGO := $(shell command -v tinygo 2> /dev/null)

.PHONY: test build clean

test:
	go test ./...

build:
ifdef TINYGO
	tinygo build -opt=2 -scheduler=none -no-debug -o $(WASM) -target wasip1 -buildmode=c-shared .
else
	GOOS=wasip1 GOARCH=wasm go build -buildmode=c-shared -o $(WASM) .
endif
	zip -j $(PLUGIN).ndp $(WASM) manifest.json

clean:
	rm -f $(WASM) $(PLUGIN).ndp
