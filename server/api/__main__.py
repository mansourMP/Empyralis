"""Entry point: python -m server.api"""
import os
import uvicorn

if __name__ == "__main__":
    is_dev = not os.getenv("EMPYRALIS_BASE_URL", "").strip()
    uvicorn.run("server.api.main:app", host="0.0.0.0", port=8000, reload=is_dev)
