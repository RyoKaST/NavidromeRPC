package main

import (
	"encoding/json"
	"fmt"
	"strings"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"github.com/navidrome/navidrome/plugins/pdk/go/scrobbler"
)

const (
	bridgeURLKey    = "bridgeUrl"
	bridgeSecretKey = "bridgeSecret"
	usernameKey     = "username"
	publicCoversKey = "publicCovers"

	sendTimeoutMs = 5000
)

type config struct {
	bridgeURL    string
	bridgeSecret string
	username     string
	publicCovers bool
}

type presencePlugin struct{}

func init() {
	scrobbler.Register(&presencePlugin{})
}

func loadConfig() (config, bool) {
	bridgeURL, _ := pdk.GetConfig(bridgeURLKey)
	bridgeSecret, _ := pdk.GetConfig(bridgeSecretKey)
	username, _ := pdk.GetConfig(usernameKey)
	publicCovers, _ := pdk.GetConfig(publicCoversKey)
	cfg := config{
		bridgeURL:    strings.TrimRight(bridgeURL, "/"),
		bridgeSecret: bridgeSecret,
		username:     username,
		publicCovers: publicCovers == "true",
	}
	ok := cfg.bridgeURL != "" && cfg.bridgeSecret != "" && cfg.username != ""
	if !ok {
		pdk.Log(pdk.LogWarn, "bridgeUrl, bridgeSecret and username are all required")
	}
	return cfg, ok
}

func (p *presencePlugin) IsAuthorized(input scrobbler.IsAuthorizedRequest) (bool, error) {
	cfg, ok := loadConfig()
	return ok && input.Username == cfg.username, nil
}

func (p *presencePlugin) NowPlaying(_ scrobbler.NowPlayingRequest) error {
	return nil
}

func (p *presencePlugin) Scrobble(_ scrobbler.ScrobbleRequest) error {
	return nil
}

func (p *presencePlugin) PlaybackReport(input scrobbler.PlaybackReportRequest) error {
	cfg, ok := loadConfig()
	if !ok || input.Username != cfg.username {
		return nil
	}
	switch input.State {
	case "starting", "playing", "paused", "stopped", "expired":
		return send(cfg, newBridgeEvent(input, cfg.publicCovers))
	default:
		return nil
	}
}

func send(cfg config, event bridgeEvent) error {
	body, err := json.Marshal(event)
	if err != nil {
		pdk.Log(pdk.LogError, fmt.Sprintf("could not encode event: %v", err))
		return scrobbler.ScrobblerErrorUnrecoverable
	}
	resp, err := host.HTTPSend(host.HTTPRequest{
		Method: "POST",
		URL:    cfg.bridgeURL + "/event",
		Headers: map[string]string{
			"Authorization": "Bearer " + cfg.bridgeSecret,
			"Content-Type":  "application/json",
		},
		Body:      body,
		TimeoutMs: sendTimeoutMs,
	})
	if err != nil {
		pdk.Log(pdk.LogWarn, fmt.Sprintf("bridge unreachable: %v", err))
		return scrobbler.ScrobblerErrorRetryLater
	}
	switch {
	case resp.StatusCode == 401:
		pdk.Log(pdk.LogError, "bridge rejected the secret, check that it matches BRIDGE_SECRET")
		return scrobbler.ScrobblerErrorUnrecoverable
	case resp.StatusCode >= 500:
		pdk.Log(pdk.LogWarn, fmt.Sprintf("bridge returned HTTP %d", resp.StatusCode))
		return scrobbler.ScrobblerErrorRetryLater
	case resp.StatusCode >= 400:
		pdk.Log(pdk.LogError, fmt.Sprintf("bridge refused the event: HTTP %d", resp.StatusCode))
		return scrobbler.ScrobblerErrorUnrecoverable
	}
	return nil
}

func main() {}
