"""
WHAT:
    Wires Phase 2.5's health check into an actual endpoint - used
    locally now, and by the cloud host's monitoring in Phase 11.

WHY THE LLM IS REPORTED HERE:
    A reachable database is not the same as a working assistant. While
    real customer data is configured the provider chain is one deep, on
    a credit with an expiry date - so the service can be perfectly
    healthy by every other measure and days from answering nothing at
    all. Monitoring should learn that before users do.

DELIBERATELY VAGUE, for the same reason check_database_health is:
    /health is unauthenticated, so anything returned here is readable by
    anyone who can reach the service. A level is enough for a monitor to
    alert on. The provider names, the day the credit lapses and the
    reasons all go to the LOGS, where operators can see them and
    strangers cannot - telling the internet the exact date this service
    stops working is not a liveness signal.
"""

from fastapi import APIRouter

from app.agent.llm_client import assess_chain
from app.db.connection import check_database_health
from app.repos.products_repo import embedding_status

router = APIRouter()

# A chain that is "at_risk" still answers every request today, so it must
# not read as an outage to a monitor that pages someone. It is reported
# as ok-with-a-warning; only "critical" degrades the overall status.
_LLM_WIRE_STATUS = {"ok": "ok", "at_risk": "warning", "critical": "unavailable"}

# A contaminated vector collection is a DEGRADED search, not a dead
# service - every other feature still answers, and so does semantic
# search for the products still in the index. It is reported so a monitor
# can alert on it; only a wholly unusable collection degrades the overall
# status, on the same reasoning as the LLM chain above.
_EMBEDDING_WIRE_STATUS = {
    "ok": "ok", "empty": "warning", "degraded": "warning",
    "broken": "unavailable",
}


@router.get("/health")
async def health():
    db_health = await check_database_health()
    chain = assess_chain()
    llm_status = _LLM_WIRE_STATUS[chain.level]

    # COUNTS, NOT A SCAN - see products_repo.embedding_status. This
    # endpoint is polled by Render and by the container healthcheck, so
    # it has to stay cheap. Never fatal to the response: a failed
    # integrity read is reported as unknown rather than taking /health
    # down, since liveness does not depend on it.
    try:
        embeddings = await embedding_status()
        embedding_wire = _EMBEDDING_WIRE_STATUS[embeddings["level"]]
        contaminated = embeddings["contaminated"]
    except Exception:
        embedding_wire, contaminated = "unknown", 0

    degraded = (
        db_health["status"] != "connected"
        or chain.level == "critical"
        or embedding_wire == "unavailable"
    )
    return {
        "status": "degraded" if degraded else "ok",
        "database": db_health,
        "llm": {"status": llm_status, "redundant": chain.redundant},
        # A COUNT, NOT THE IDS. /health is unauthenticated, so this says
        # how much is wrong and nothing about what - the ids go to the
        # logs and to scripts/check_embeddings.py, where an operator can
        # see them and a stranger cannot.
        "embeddings": {"status": embedding_wire, "contaminated": contaminated},
    }