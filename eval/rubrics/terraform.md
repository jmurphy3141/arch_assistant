# Terraform bundle quality rubric

## Dimension: infrastructure_fidelity
5 = Resources and relationships implement the fixture architecture exactly, with no missing tiers or invented services.
3 = Core infrastructure is present but some details require manual completion.
1 = The bundle materially diverges from the requested architecture.

## Dimension: module_and_variable_design
5 = Files, variables, locals, outputs, naming, and dependencies form a coherent reusable module.
3 = The code is organized but has hardcoded values or weak interfaces.
1 = The bundle is monolithic, brittle, or incomplete.

## Dimension: security_and_operability
5 = Least privilege, private networking, secrets handling, tagging, observability, and lifecycle concerns are designed explicitly.
3 = Basic security is present but important operational controls are omitted.
1 = The code embeds unsafe defaults, credentials, or publicly exposes protected tiers.

## Dimension: correctness_and_idiomatic_hcl
5 = Terraform validates cleanly and uses correct OCI resources, arguments, references, and dependency patterns.
3 = The design is plausible but needs small syntax or provider-schema fixes.
1 = The HCL is invalid or relies on invented OCI resource types.

## Dimension: handoff_documentation
5 = README, inputs, outputs, prerequisites, assumptions, and safe deployment steps enable another engineer to operate the bundle.
3 = Some documentation exists but important setup or assumptions are missing.
1 = The bundle has little usable handoff guidance.
