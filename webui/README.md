# LangLang Web UI

This directory contains the Vue 3 interface used by the new Qt WebEngine shell.

```powershell
npm install
npm run build
```

The Python host serves `dist/` from a loopback-only HTTP server and requires a
random per-process session token for every `/api/action` request.
