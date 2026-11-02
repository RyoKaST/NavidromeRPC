package main

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"github.com/navidrome/navidrome/plugins/pdk/go/scrobbler"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

func setup(t *testing.T, config map[string]string) {
	t.Helper()
	pdk.ResetMock()
	host.HTTPMock.ExpectedCalls = nil
	host.HTTPMock.Calls = nil
	host.ArtworkMock.ExpectedCalls = nil
	host.ArtworkMock.Calls = nil
	pdk.PDKMock.On("Log", mock.Anything, mock.Anything).Maybe()
	for _, key := range []string{bridgeURLKey, bridgeSecretKey, usernameKey, publicCoversKey} {
		value, ok := config[key]
		pdk.PDKMock.On("GetConfig", key).Return(value, ok).Maybe()
	}
}

func fullConfig() map[string]string {
	return map[string]string{
		bridgeURLKey:    "http://192.168.1.100:6464/",
		bridgeSecretKey: "0123456789abcdef",
		usernameKey:     "Lukas",
	}
}

func report(state string) scrobbler.PlaybackReportRequest {
	return scrobbler.PlaybackReportRequest{
		Username:     "Lukas",
		State:        state,
		PositionMs:   20000,
		PlaybackRate: 1.5,
		Timestamp:    1000,
		Track: scrobbler.TrackInfo{
			ID:         "track1",
			Title:      "Song",
			Artist:     "Artist",
			Album:      "Album",
			Duration:   200.5,
			MBZAlbumID: "mbid-1",
		},
	}
}

func expectSend(status int32, err error) *host.HTTPRequest {
	sent := &host.HTTPRequest{}
	host.HTTPMock.On("Send", mock.Anything).Run(func(args mock.Arguments) {
		*sent = args.Get(0).(host.HTTPRequest)
	}).Return(&host.HTTPResponse{StatusCode: status}, err)
	return sent
}

func TestIsAuthorizedOnlyForTheConfiguredUser(t *testing.T) {
	setup(t, fullConfig())
	plugin := presencePlugin{}

	authorized, err := plugin.IsAuthorized(scrobbler.IsAuthorizedRequest{Username: "Lukas"})
	assert.NoError(t, err)
	assert.True(t, authorized)

	authorized, _ = plugin.IsAuthorized(scrobbler.IsAuthorizedRequest{Username: "lukas"})
	assert.False(t, authorized)
}

func TestIncompleteConfigDoesNothing(t *testing.T) {
	for _, missing := range []string{bridgeURLKey, bridgeSecretKey, usernameKey} {
		config := fullConfig()
		delete(config, missing)
		setup(t, config)
		plugin := presencePlugin{}

		authorized, err := plugin.IsAuthorized(scrobbler.IsAuthorizedRequest{Username: "Lukas"})
		assert.NoError(t, err)
		assert.False(t, authorized)
		assert.NoError(t, plugin.PlaybackReport(report("playing")))
		host.HTTPMock.AssertNotCalled(t, "Send", mock.Anything)
	}
}

func TestIncompleteConfigIsLogged(t *testing.T) {
	config := fullConfig()
	delete(config, bridgeURLKey)
	setup(t, config)

	(&presencePlugin{}).IsAuthorized(scrobbler.IsAuthorizedRequest{Username: "Lukas"})
	pdk.PDKMock.AssertCalled(t, "Log", pdk.LogWarn, mock.Anything)
}

func TestOtherUsersAreIgnored(t *testing.T) {
	setup(t, fullConfig())
	input := report("playing")
	input.Username = "Someone"

	assert.NoError(t, (&presencePlugin{}).PlaybackReport(input))
	host.HTTPMock.AssertNotCalled(t, "Send", mock.Anything)
}

func TestUnknownStatesAreIgnored(t *testing.T) {
	setup(t, fullConfig())

	assert.NoError(t, (&presencePlugin{}).PlaybackReport(report("buffering")))
	host.HTTPMock.AssertNotCalled(t, "Send", mock.Anything)
}

func TestPlaybackReportSendsTheEvent(t *testing.T) {
	setup(t, fullConfig())
	sent := expectSend(204, nil)

	assert.NoError(t, (&presencePlugin{}).PlaybackReport(report("playing")))

	assert.Equal(t, "POST", sent.Method)
	assert.Equal(t, "http://192.168.1.100:6464/event", sent.URL)
	assert.Equal(t, "Bearer 0123456789abcdef", sent.Headers["Authorization"])
	assert.Equal(t, "application/json", sent.Headers["Content-Type"])

	var body map[string]any
	assert.NoError(t, json.Unmarshal(sent.Body, &body))
	assert.Equal(t, map[string]any{
		"state":        "playing",
		"title":        "Song",
		"artist":       "Artist",
		"album":        "Album",
		"durationMs":   float64(200500),
		"positionMs":   float64(20000),
		"playbackRate": 1.5,
		"timestamp":    float64(1000),
		"coverUrl":     "https://coverartarchive.org/release/mbid-1/front-500",
	}, body)
}

func TestEveryKnownStateIsSent(t *testing.T) {
	for _, state := range []string{"starting", "playing", "paused", "stopped", "expired"} {
		setup(t, fullConfig())
		expectSend(204, nil)
		assert.NoError(t, (&presencePlugin{}).PlaybackReport(report(state)))
		host.HTTPMock.AssertNumberOfCalls(t, "Send", 1)
	}
}

func TestPublicCoversUseNavidromeArtwork(t *testing.T) {
	config := fullConfig()
	config[publicCoversKey] = "true"
	setup(t, config)
	host.ArtworkMock.On("GetTrackUrl", "track1", int32(coverSize)).Return("https://music.example.org/art.jpg", nil)

	track := report("playing").Track
	assert.Equal(t, "https://music.example.org/art.jpg", coverURL(track, true))
}

func TestCoverFallsBackWhenArtworkFails(t *testing.T) {
	setup(t, fullConfig())
	host.ArtworkMock.On("GetTrackUrl", "track1", int32(coverSize)).Return("", errors.New("boom"))

	track := report("playing").Track
	assert.Equal(t, "https://coverartarchive.org/release/mbid-1/front-500", coverURL(track, true))
}

func TestNoCoverWithoutMusicBrainzID(t *testing.T) {
	assert.Equal(t, "", coverURL(scrobbler.TrackInfo{ID: "track1"}, false))
}

func TestBridgeErrorsMapToScrobblerErrors(t *testing.T) {
	cases := []struct {
		status int32
		err    error
		want   error
	}{
		{204, nil, nil},
		{401, nil, scrobbler.ScrobblerErrorUnrecoverable},
		{400, nil, scrobbler.ScrobblerErrorUnrecoverable},
		{503, nil, scrobbler.ScrobblerErrorRetryLater},
		{0, errors.New("connection refused"), scrobbler.ScrobblerErrorRetryLater},
	}
	for _, c := range cases {
		setup(t, fullConfig())
		expectSend(c.status, c.err)
		err := (&presencePlugin{}).PlaybackReport(report("playing"))
		if c.want == nil {
			assert.NoError(t, err)
		} else {
			assert.ErrorIs(t, err, c.want)
		}
	}
}
