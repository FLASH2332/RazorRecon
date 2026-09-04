from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routers import sessions, upload, reconcile, report

app = FastAPI(
    title="RazorRecon API",
    description="AI Finance Controller — Razorpay Settlement Reconciliation",
    version="0.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

app.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
app.include_router(upload.router, prefix="/sessions", tags=["upload"])
app.include_router(reconcile.router, prefix="/sessions", tags=["reconcile"])
app.include_router(report.router, prefix="/sessions", tags=["report"])

@app.get("/health")
def health():
    return {"status": "ok", "service": "razorrecon"}