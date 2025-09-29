
"""Entry point for StreamRip radio service."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "radio_service.server:app",
        host="127.0.0.1",
        port=8001,  # Different port from admin service
        log_level="info",
        reload=True  # Enable auto-reload during development
    )