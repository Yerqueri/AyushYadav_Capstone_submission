"""FastAPI application — POST /tickets runs the full synchronous triage pipeline."""
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from src.coordinator import run_pipeline
from src.ingest import normalize_ticket
from src.logging_store import get_engine
from src.metrics import PIPELINE_LATENCY, TICKETS_TOTAL, init_metrics_server
from src.models import TriageResponse
from src.retrieve import get_collection

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=__import__("os").getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_collection()       # build ChromaDB from documentation.json at startup
    init_metrics_server()  # start Prometheus on :8001
    get_engine()           # create storage/ dir and decisions table
    yield


app = FastAPI(title="CloudServe Support Triage", version="1.0.0", lifespan=lifespan)


@app.post("/tickets", response_model=TriageResponse)
def triage_ticket(raw: dict) -> TriageResponse:
    """Classify, retrieve, and draft (or escalate) a support ticket synchronously."""
    ticket = normalize_ticket(raw)
    with PIPELINE_LATENCY.time():
        response = run_pipeline(ticket)
    TICKETS_TOTAL.labels(channel=ticket.channel, route=response.route).inc()
    return response


@app.post("/tickets/batch", response_model=list[TriageResponse])
def triage_tickets_batch(raw_tickets: list[dict]) -> list[TriageResponse]:
    """Classify, retrieve, and draft (or escalate) a batch of support tickets synchronously."""
    responses = []
    for raw in raw_tickets:
        ticket = normalize_ticket(raw)
        with PIPELINE_LATENCY.time():
            res = run_pipeline(ticket)
        TICKETS_TOTAL.labels(channel=ticket.channel, route=res.route).inc()
        responses.append(res)
    return responses



@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled pipeline error: %s", exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "internal_server_error"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=False)
