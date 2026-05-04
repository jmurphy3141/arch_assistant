# BOM Sub-Agent

Produces priced OCI Bills of Materials.

## Run
python3.11 -m uvicorn sub_agents.bom.server:app --port 8083

## Card
GET http://localhost:8083/a2a/card

## Call
POST http://localhost:8083/a2a
{"task": "Size a 3-tier web application: 4 OCPUs compute, 64GB RAM, 10TB block storage, us-chicago-1"}
