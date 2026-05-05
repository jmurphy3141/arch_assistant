# POV Sub-Agent

Writes OCI Point-of-View documents.

## Run
python3.11 -m uvicorn sub_agents.pov.server:app --port 8084

## Card
GET http://localhost:8084/a2a/card

## Call
POST http://localhost:8084/a2a
{"task": "Write a POV for Acme Corp migrating their 3-tier web app to OCI Chicago region..."}
