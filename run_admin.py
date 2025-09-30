"""Entry point for StreamRip admin service."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "admin_service.server:app",
        host="0.0.0.0",  # Bind to all network interfaces (accessible via public IP)
        port=8000,
        log_level="info",
        reload=True  # Enable auto-reload during development
    )