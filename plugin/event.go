package main

import (
	"fmt"

	"github.com/navidrome/navidrome/plugins/pdk/go/host"
	"github.com/navidrome/navidrome/plugins/pdk/go/pdk"
	"github.com/navidrome/navidrome/plugins/pdk/go/scrobbler"
)

const coverSize = 512

type bridgeEvent struct {
	State        string  `json:"state"`
	Title        string  `json:"title"`
	Artist       string  `json:"artist"`
	Album        string  `json:"album"`
	DurationMs   int64   `json:"durationMs"`
	PositionMs   int64   `json:"positionMs"`
	PlaybackRate float64 `json:"playbackRate"`
	Timestamp    int64   `json:"timestamp"`
	CoverURL     string  `json:"coverUrl"`
}

func newBridgeEvent(input scrobbler.PlaybackReportRequest, publicCovers bool) bridgeEvent {
	return bridgeEvent{
		State:        input.State,
		Title:        input.Track.Title,
		Artist:       input.Track.Artist,
		Album:        input.Track.Album,
		DurationMs:   int64(input.Track.Duration * 1000),
		PositionMs:   input.PositionMs,
		PlaybackRate: input.PlaybackRate,
		Timestamp:    input.Timestamp,
		CoverURL:     coverURL(input.Track, publicCovers),
	}
}

func coverURL(track scrobbler.TrackInfo, publicCovers bool) string {
	if publicCovers {
		url, err := host.ArtworkGetTrackUrl(track.ID, coverSize)
		if err == nil && url != "" {
			return url
		}
		pdk.Log(pdk.LogWarn, fmt.Sprintf("no Navidrome artwork for track %s: %v", track.ID, err))
	}
	if track.MBZAlbumID != "" {
		return fmt.Sprintf("https://coverartarchive.org/release/%s/front-500", track.MBZAlbumID)
	}
	return ""
}
