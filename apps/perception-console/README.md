# Local Perception Console

The console is the formal loopback UI for home, vision, and voice status. The
Python gateway binds to `127.0.0.1:8770`, serves the built React assets, and
proxies only explicit allow-listed routes to ports 8765–8767.

## Source layout

- `src/main.py`: static host, unified health, and HTTP/SSE proxy.
- `src/proxy.py`: service map, route allow-list, and 1 MiB request limit.
- `web/`: React + TypeScript + Vite source.
- `web/dist/`: generated production assets; intentionally not authored by hand.

## Build the UI

The implementation agent did not install packages or run a build, per the
project's user-owned verification constraint. When you choose to build it:

```powershell
cd E:\smart-home\apps\perception-console\web
npm install
npm run build
```

Then run the gateway from the repository root:

```powershell
.\.venv\Scripts\python.exe apps\perception-console\src\main.py
```

Before `web/dist` exists, `/api/*` remains available while `/` returns an honest
`frontend_not_built` response instead of a blank or misleading page.

For source UI development, run the gateway on 8770 and `npm run dev`; Vite
proxies `/api` to the gateway. All services remain loopback-only.
