# Architecture diagram quality rubric

## Dimension: architectural_clarity
5 = Boundaries, tiers, components, and primary flows are immediately understandable without narration.
3 = The architecture is understandable but some relationships or boundaries are ambiguous.
1 = The visual structure obscures how the system works.

## Dimension: oci_specificity
5 = OCI services, icons/labels, network constructs, and deployment semantics are precise and appropriate.
3 = Most services are recognizable but some labels or constructs are generic.
1 = The diagram could represent any cloud or uses misleading OCI components.

## Dimension: grounding_and_parity
5 = Every depicted service, quantity, tier, region, and connection matches the engagement fixture with no invented topology.
3 = The main design matches but minor details are omitted or weakly grounded.
1 = The diagram contradicts the fixture or invents material services.

## Dimension: visual_hierarchy
5 = Layout, grouping, spacing, and connector routing create a polished executive-to-engineer reading path.
3 = The layout is serviceable but crowded, uneven, or visually flat.
1 = Overlaps, crossings, or poor grouping make the diagram difficult to use.

## Dimension: delivery_readiness
5 = The diagram is customer-ready, legible, technically reviewable, and needs no manual repair.
3 = It is usable after modest visual cleanup.
1 = It is a draft data dump rather than a deliverable.
