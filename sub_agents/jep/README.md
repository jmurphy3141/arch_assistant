# JEP Sub-Agent

Writes OCI Joint Engagement Plan documents.

## Run
python3.11 -m uvicorn sub_agents.jep.server:app --port 8085

## Card
GET http://localhost:8085/a2a/card

## Call
POST http://localhost:8085/a2a
{"task": "Write a JEP for Acme Corp OCI migration engagement..."}
