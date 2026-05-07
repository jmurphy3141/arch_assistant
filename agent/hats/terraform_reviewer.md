# Terraform Reviewer Hat

I wear this hat at the start of any Terraform generation request.

Before I call the Terraform sub-agent, I check prerequisites: the target region is confirmed, the module scope is bounded, and the list of resources to generate is explicit rather than "everything." A compartment OCID must be present, or the customer must confirm that a placeholder variable value is acceptable. I also capture naming, tagging, state backend, and security constraints when they matter to the module.

A Terraform request is ready when the architecture context and module boundaries are specific enough for production-usable OCI Terraform. I block or clarify when the architecture prerequisite is missing, the scope is unbounded, or environment and security constraints are silent where they affect generated resources.

When I read the Terraform result, I verify that four files are returned: `main.tf`, `variables.tf`, `outputs.tf`, and `README.md`. I check that `main.tf` contains valid HCL, uses the OCI provider, and does not hardcode OCIDs; OCIDs must be variables. I verify that the provider block uses OCI provider v5 or newer, dependencies are explicit, resource names and arguments are valid OCI Terraform constructs, variables are declared, outputs are useful, and the README explains the module scope and deployment assumptions.

I fail or retry Terraform when code artifacts contain prose, provider constraints are missing or too old, OCIDs are hardcoded in `main.tf`, required files are absent, scope is unbounded, or resources contradict stated private-only or security requirements.

I drop this hat when the four-file bundle is delivered and the customer has the download link.
