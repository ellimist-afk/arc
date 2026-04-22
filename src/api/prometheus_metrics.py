"""
Prometheus Metrics - Stub Implementation
"""
from starlette.responses import PlainTextResponse


async def metrics_endpoint() -> PlainTextResponse:
    """Return Prometheus metrics."""
    metrics = """# HELP talkbot_requests_total Total requests
# TYPE talkbot_requests_total counter
talkbot_requests_total 0

# HELP talkbot_up Service up status
# TYPE talkbot_up gauge
talkbot_up 1
"""
    return PlainTextResponse(metrics, media_type="text/plain")
