## EC2 Sizing Guidance

Use `size_ec2` when the user describes a workload and needs instance recommendations.

**Required arg:** `workload` — describe the application tier, expected traffic,
and any specific requirements (e.g. "3-tier web app, 5k concurrent users,
stateless API tier").

**When to call:** After you understand the workload characteristics. Do not
call without at least a basic workload description.

**After sizing:** Offer to generate CloudFormation (`generate_cfn`) to
provision the recommended infrastructure.
