# OCI Enterprise AI Deployer Feature History

- **Author:** L. Saetta
- **Version:** 0.9.0
- **Last modified:** 2026-05-05
- **License:** MIT

This document tracks the functional story of the deployer as features are added
over time. It is intentionally more narrative than a release changelog: the goal
is to keep a clear memory of why each capability was introduced.

## 2026-05-05 - Version 0.9.0

Version 0.9.0 marks the project as ready for broader validation of the full
deployment workflow.

Release focus:

- Consolidated the CLI deployment flow for OCI Enterprise AI Hosted
  Applications and Hosted Deployments.
- Included the Web UI operational path with backend validation, dry-run,
  rendering, build, deploy, and streamed progress.
- Added Linux Web UI deployment documentation for Conda, npm, firewall, API,
  and frontend startup.
- Added optional Web UI to API shared-key protection for trusted-network usage.
- Preserved version 1 design constraints while preparing the project for
  field testing before a future 1.0 release.

## 2026-05-05 - Compartment Name Resolution

Deployment YAML can now identify the shared OCI compartment by display name
instead of OCID.

New capabilities:

- Added `compartment_name` as an alternative to `compartment_id` for both
  legacy single-deployment YAML and Enterprise Solution YAML.
- Enforced that exactly one of `compartment_id` or `compartment_name` is
  provided.
- Resolved `compartment_name` through OCI IAM lookup before validation,
  rendering, dry-run, build, or deployment proceeds.
- Reused the same resolver in the command-line workflow and Web UI backend.
- Preserved the interactive menu behavior that lets users choose when multiple
  compartments share the same name.

## 2026-05-05 - Web UI API Key Protection

The Web UI and FastAPI backend now support an optional shared API key for
development and trusted-network deployments.

New capabilities:

- Added `DEPLOYER_WEB_API_KEY` support to the FastAPI backend.
- Protected Web UI API endpoints with the `X-API-Key` header when a backend key
  is configured.
- Kept local development backward-compatible when no API key is configured.
- Added `NEXT_PUBLIC_DEPLOYER_API_KEY` support to the Next.js Web UI.
- Replaced browser `EventSource` streaming with `fetch`-based streaming so the
  API key is sent in headers instead of URL query parameters.
- Added tests for missing, invalid, and valid API key requests.
- Updated Linux Web UI deployment documentation with API key start commands.

Example:

```bash
DEPLOYER_WEB_API_KEY='replace-with-a-long-random-value' \
DEPLOYER_WEB_CORS_ORIGINS='*' \
python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 0.0.0.0 \
  --port 8100
```

```bash
NEXT_PUBLIC_DEPLOYER_API_KEY='replace-with-a-long-random-value' \
NEXT_PUBLIC_DEPLOYER_API_URL=http://192.168.1.25:8100 \
npm run dev -- --hostname 0.0.0.0
```

This is a lightweight protection mechanism for development or LAN usage. It is
not intended to replace a production identity provider, because `NEXT_PUBLIC_`
frontend values are visible to the browser.

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
