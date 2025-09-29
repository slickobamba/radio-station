"""Entry point for StreamRip admin service."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "admin_service.server:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
        reload=True  # Enable auto-reload during development
    )