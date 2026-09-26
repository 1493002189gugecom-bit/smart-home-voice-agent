# Local Perception Console

The console is the formal loopback UI for home, vision, and voice status. The
Python gateway binds to `127.0.0.1:8770`, serves the built React assets, and
proxies only explicit allow-listed routes to ports 8765–8767.
`GET /health` checks only the console process; `GET /api/status` aggregates
backend status and can take longer when a backend is slow.

## Source layout

- `src/main.py`: static host, unified health, and HTTP/SSE proxy.
- `src/proxy.py`: service map, route allow-list, and 1 MiB request limit.
- The vision view uses a continuous `/api/vision/preview.mjpeg` stream; the gateway
  relays each JPEG part without saving frames.
- `web/`: React + TypeScript + Vite source.
- `web/dist/`: generated production assets; intentionally not authored by hand.

## Build the UI

Build the checked-in React source with:

```powershell
cd E:\smart-home\apps\perception-console\web
npm ci
npm run build
```

Then run the gateway from the repository root:

```powershell
.\.venv\Scripts\python.exe apps\perception-console\src\main.py
```

Before `web/dist` exists, `/api/*` remains available while `/` returns an honest
`frontend_not_built` response instead of a blank or misleading page.

For normal Windows use, run `tools\start-perception.ps1`. It starts missing
services, reports each failure independently, and opens the console in Edge or
Chrome app mode when port 8770 is healthy even if another service failed.
The Services page separates HTTP availability, model readiness and recent camera
frames; it also shows the last successful backend response. `tools\stop-perception.ps1` stops this repository's four
services, including manually started instances on their designated ports, and
fails if a port remains occupied.

For source UI development, run the gateway on 8770 and `npm run dev`; Vite
proxies `/api` to the gateway. All services remain loopback-only.

## Runtime behavior

- Identity enrollment shows the selected camera's live preview while a session is
  active. Cancel the session before changing cameras; the preview stream is
  relayed in memory and camera frames are not saved by the console.
- Audio starts in automatic mode and follows the Windows system default when it
  is usable. If it is unavailable, the service reports its fallback. A manual
  choice is matched by device name and host API and stored in the ignored local
  file `runtime/voice-agent/audio-devices.json`; device indexes are not saved.
- After applying an audio choice, wait for the UI to confirm that the service
  applied it or report a failure. A pending request is not shown as active.
- The console does not persist raw audio, transcripts, or assistant replies.
