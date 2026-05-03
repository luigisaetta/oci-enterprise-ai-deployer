# OCI Enterprise AI Deployer Feature History

- **Author:** L. Saetta
- **Version:** 0.1.0
- **Last modified:** 2026-05-03
- **License:** MIT

This document tracks the functional story of the deployer as features are added
over time. It is intentionally more narrative than a release changelog: the goal
is to keep a clear memory of why each capability was introduced.

## 2026-05-03 - Rollback to Immutable Image Tag

The deployer now supports a basic rollback workflow based on immutable container
image tags.

New capabilities:

- Added implementation for `rollback --to-tag <tag>`.
- Calculated rollback image URIs without modifying the YAML configuration file.
- Reused the existing Hosted Application by resolving it from the configured
  Hosted Application display name.
- Created a new Hosted Deployment that points to the requested image tag.
- Preserved serial execution for Enterprise Solution YAML files with multiple
  deployments.
- Stopped rollback execution immediately on the first deployment failure.
- Added `--dry-run` support so rollback commands can be reviewed before OCI
  resources are changed.
- Added tests for dry-run rollback, successful Hosted Deployment creation, and
  missing Hosted Application failure handling.
- Documented rollback usage in the main README.

Example:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  rollback --to-tag abc1234
```

## 2026-05-02 - Enterprise Solution Deployments

The deployer was extended from a single-deployment YAML model to an Enterprise
Solution model.

New capabilities:

- Added a top-level `enterprise_solution` YAML section.
- Added a `deployments` list for defining one or more deployment units.
- Kept `region`, `region_key`, and `compartment_id` at Enterprise Solution
  level, so they are declared once and inherited by every deployment.
- Enforced the design constraint that one Enterprise Solution is confined to one
  OCI region and one OCI compartment.
- Preserved the one-to-one relationship between a deployment and its dedicated
  Hosted Application.
- Preserved the one-to-one relationship between that Hosted Application and its
  Hosted Deployment.
- Implemented serial execution for multi-deployment solutions.
- Stopped execution immediately on the first deployment failure.
- Added failure output that identifies the Enterprise Solution, deployment, and
  failing phase.
- Wrote generated JSON artifacts for multi-deployment YAML under
  deployment-specific output directories.
- Preserved compatibility with the legacy single-deployment YAML format.

Example:

```yaml
enterprise_solution:
  name: my-enterprise-ai-solution-dev
  compartment_id: ocid1.compartment.oc1..example
  region: eu-frankfurt-1
  region_key: fra

deployments:
  - name: agent-api
    container:
      context: ../../examples/hello_world_container
      dockerfile: Dockerfile
      image_repository: ai-agents/enterprise-ai-agent-api
      tag_strategy: git_sha
      ocir_namespace: auto
    hosted_application:
      display_name: my-agent-api-app-dev
      security:
        auth_type: IDCS_AUTH_CONFIG
        issuer_url: https://idcs.example.identity.oraclecloud.com:443
        audience: my-agent-api
        scopes:
          - my-agent-api/.default
    hosted_deployment:
      display_name: my-agent-api-deployment-dev
      create_new_version: true
      activate: true
      wait_for_state: SUCCEEDED
```

The full editable example lives in:

```text
enterprise_ai_deployment/examples/enterprise_solution_dev.yaml
```
