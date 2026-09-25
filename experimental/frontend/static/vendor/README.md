# Vendor Directory - Offline CDN Assets

This directory contains local copies of external dependencies for offline functionality.

## Purpose

The application uses a "local-first with CDN fallback" strategy:
- **Online**: Tries to load from local vendor files first
- **Offline**: Falls back to CDN if local files fail or are missing
- **No vendor files**: Automatically falls back to CDN URLs

This will download:
- **Tailwind CSS** - Utility-first CSS framework
- **DaisyUI** - Tailwind component library
- **HTMX** - HTML-over-the-wire library
- **Alpine.js** - Lightweight JavaScript framework
- **Video.js** - HTML5 video player
- **Swagger UI** - API documentation interface
- **ReDoc** - API documentation alternative

## File Structure

```
vendor/
├── css/
│   ├── daisyui.min.css
│   ├── video-js.css
│   └── swagger-ui.css
└── js/
    ├── tailwind.js
    ├── htmx.min.js
    ├── alpine.min.js
    ├── video.min.js
    ├── swagger-ui-bundle.js
    └── redoc.standalone.js
```

## Notes

- Vendor files are **optional** - the app works without them using CDN
- Vendor files are **gitignored** by default to reduce repository size
- Download vendor files for:
  - Offline development/deployment
  - Air-gapped environments
  - Reducing external dependencies
  - Faster page loads (no CDN latency)