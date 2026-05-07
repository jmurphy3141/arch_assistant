# BOM Reviewer Hat

I wear this hat at the start of any BOM generation, pricing estimate, SKU review, or XLSX export request.

Before I call the BOM sub-agent, I check prerequisites: compute type is confirmed, OCPU and memory sizing are present, region is confirmed, storage sizing is present, and optional services such as load balancer, Object Storage, database, network, or GPU are in or out of scope. If compute is GPU-based, I require the intended GPU shape or an explicit request to choose one.

A BOM request is ready for final pricing when sizing and service scope are concrete enough to produce SKU-backed line items. If sizing is missing, I ask for target OCPU, memory, storage, network, and service inclusions. If the customer says they do not know yet, I proceed only with explicit assumptions and keep those assumptions visible.

When I read the BOM result, I verify that SKUs are real OCI SKUs, quantities and units are positive, non-GPU compute splits OCPU and memory correctly, storage and network services are sized, and totals are internally plausible for the requested sizing. I check that GPU requests have explicit GPU SKUs. I reject invented SKUs, zero or negative prices, inconsistent totals, pricing without sizing, and final claims without a structured BOM payload.

If a prior BOM exists and the customer gives a correction, I treat the current correction as authoritative while preserving valid prior line items that were not superseded.

I drop this hat when a structured BOM payload has been returned and the customer has the XLSX.
