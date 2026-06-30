"""Entry point: python -m server.api"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("server.api.main:app", host="0.0.0.0", port=8000, reload=True)
