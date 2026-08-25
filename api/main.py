from fastapi import FastAPI

app = FastAPI(title="RazorRecon")

@app.get("/health")
def health():
    return {"status": "ok"}