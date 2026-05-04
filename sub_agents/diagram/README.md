# Diagram Sub-Agent

Generates OCI architecture draw.io diagrams.

## Run
python3.11 -m uvicorn sub_agents.diagram.server:app --port 8082

## Card
GET http://localhost:8082/a2a/card

## Call
POST http://localhost:8082/a2a
{"task": "3-tier web app with load balancer, 2 app servers, ATP database"}
