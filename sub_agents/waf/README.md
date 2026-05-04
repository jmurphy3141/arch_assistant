# WAF Sub-Agent

Performs OCI Well-Architected Framework reviews.

## Run
python3.11 -m uvicorn sub_agents.waf.server:app --port 8086

## Card
GET http://localhost:8086/a2a/card

## Call
POST http://localhost:8086/a2a
{"task": "Review this OCI architecture for WAF compliance...",
 "engagement_context": {"architecture_summary": "..."}}
