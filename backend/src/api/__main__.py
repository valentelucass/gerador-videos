import uvicorn

uvicorn.run("backend.src.api.app:app", host="127.0.0.1", port=8000, reload=True)
