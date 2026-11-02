# NavidromeRPC

Shows what you're playing on Navidrome on your Discord profile, as a real "Listening to" status. From any player, including your phone.

No user token, unlike the other plugins. It just uses a logged-in Discord client.

```
Navidrome + plugin --> bridge --> Vesktop --> your Discord profile
```

The plugin tells the bridge what's playing, and the bridge passes it to a Discord client through its local Rich Presence socket. That client is either one running in Docker or the one already open on your PC.

## Pick your setup

| Your situation | Use | Works when |
|---|---|---|
| Navidrome on a home server or NAS | [Docker](#docker-setup) | Always, even with your PC off |
| Navidrome on a VPS | [Docker](#docker-setup), then read the [VPS note](#vps) | Always |
| You'd rather not run another container, and you're on Linux | [PC](#pc-setup) | While Discord is open on that PC |

Navidrome can run anywhere for the PC setup, as long as it can reach your PC on your network.

Docker runs a second Discord client, so it costs [about 700 MB of RAM](#stats). The PC setup reuses the one you already have.

Whichever you choose, you need a Discord app first. It takes a minute:

1. Open the [Developer Portal](https://discord.com/developers/applications) and create an application. Its name can show on your profile, so `Navidrome` is a good pick.
2. Copy the **Application ID**.

## Docker setup

Needs Docker on an `amd64` machine, so no Raspberry Pi for now.

```sh
git clone https://github.com/RyoKaST/NavidromeRPC
cd NavidromeRPC
cat > .env <<EOF
CLIENT_ID=your_application_id
BRIDGE_SECRET=$(openssl rand -hex 24)
LOGIN=1
EOF
docker compose up -d --build
```

Log in to Discord once. The login screen is only reachable from the server itself, so tunnel to it from your PC:

```sh
ssh -N -L 6080:127.0.0.1:6080 you@your-server
```

Open <http://localhost:6080/vnc.html>, click Connect and log in. When your chats appear, close the tab and turn the login screen off:

```sh
sed -i 's/LOGIN=1/LOGIN=0/' .env
docker compose up -d
```

Treat the container's volume like a password, since it holds your logged-in session. Now [install the plugin](#install-the-plugin), using your server's LAN IP.

## PC setup

Linux only for now, with Python 3. Tested with Vesktop, where the **Rich Presence** option has to be on. The official Discord app should work too, but I haven't tried it.

```sh
git clone https://github.com/RyoKaST/NavidromeRPC
cd NavidromeRPC
env CLIENT_ID=your_application_id BRIDGE_SECRET=pick-16-or-more-characters python3 sidecar/bridge.py
```

Leave it running, ideally as a user service so it starts with your session. Then [install the plugin](#install-the-plugin), using your PC's LAN IP. If you have a firewall, allow port 6464 from your Navidrome machine.

## Install the plugin

1. Download `navidrome-rpc.ndp` from [Releases](https://github.com/RyoKaST/NavidromeRPC/releases) and put it in Navidrome's `plugins` folder, inside its data folder.
2. In Navidrome, open **Plugins** in the admin area, rescan and enable NavidromeRPC.
3. Give your user access. Do it here in the web UI, because `navidrome plugin edit --users` doesn't work for this.
4. Fill in the settings:

| Setting | Value |
|---|---|
| Bridge URL | `http://192.168.1.100:6464`, with your own IP |
| Bridge secret | Your `BRIDGE_SECRET` |
| Navidrome username | Your username, exactly as written in Navidrome |

The Bridge URL has to be a private IP. Navidrome blocks hostnames and `localhost` here, so even on the same machine use the LAN IP.

In Discord, turn on activity sharing under **User Settings > Activity Privacy**. Play a song and you're done.

## Album art

Discord downloads covers itself, so they need a public URL.

**If Navidrome is on the internet,** set `ND_SHAREURL=https://music.example.com` on Navidrome, then turn on **Use Navidrome artwork** in the plugin. The address must be `https`, and your reverse proxy has to allow `/share/img/`.

**If it isn't,** covers come from [Cover Art Archive](https://coverartarchive.org), which only works for albums tagged with a MusicBrainz ID. Everything else shows without a cover.

## Stats

Measured on an Unraid server with Navidrome 0.64.2.

| | Docker | PC |
|---|---|---|
| RAM idle | ~700 MB | ~17 MB, plus the Discord you already run |
| RAM while playing | ~800 MB | ~17 MB |
| CPU | ~3% of one core | Almost none |
| Disk | 1.14 GB image + ~75 MB login | None |
| Plugin size | 1.3 MB | 1.3 MB |

## Troubleshooting

**Nothing on my profile.** Start with the logs:

```sh
docker logs navidrome-rpc | grep bridge:
```

- No `event:` lines means the plugin isn't reaching the bridge. Check that the plugin is enabled, your user was picked in the web UI, and the Bridge URL is right.
- `event:` lines but no status usually means activity sharing is off, or the Discord session was logged out. Set `LOGIN=1` and log in again.

**The plugin turned off after an update.** Navidrome disables a plugin whenever its file changes. Switch it back on.

**Wrong secret.** Navidrome's log will say the bridge rejected it. The plugin and `.env` have to match exactly.

**The status stays after I stop.** Pausing keeps it on purpose. Navidrome can also take up to 30 minutes to end a session the player never closed.

## Good to know

Vesktop ships with Vencord, and Discord's rules don't allow modified clients. It's your account, so use this at your own risk.

If Discord logs the session out, the bridge can't tell, so the status just stops appearing.

## VPS

Nothing above changes, with two exceptions. Use the Docker gateway as the Bridge URL, usually `http://172.17.0.1:6464`. Port 6464 is published on all interfaces, so block it from the internet with your firewall.

## Technical details

### Bridge settings

| Variable | Default | |
|---|---|---|
| `CLIENT_ID` | | Required |
| `BRIDGE_SECRET` | | Required, 16 characters or more |
| `BRIDGE_PORT` | `6464` | Listening port, only works for the PC setup |
| `LOGIN` | `0` | Docker only, turns on the login screen |

### API

`GET /health` returns 200 while connected to Discord, otherwise 503.

`POST /event` needs `Authorization: Bearer <secret>`:

```json
{
  "state": "playing",
  "title": "Song",
  "artist": "Artist",
  "album": "Album",
  "durationMs": 200000,
  "positionMs": 20000,
  "playbackRate": 1.0,
  "timestamp": 1793651151,
  "coverUrl": "https://example.org/cover.jpg"
}
```

`state` is `starting`, `playing`, `paused`, `stopped` or `expired`. Older events are ignored, and updates are sent at most once every 2 seconds.

### Building

```sh
cd plugin
make test
make build
```

Needs Go and `zip`. It uses TinyGo if you have it. Bridge tests run with `python3 -m unittest discover -s sidecar`.
