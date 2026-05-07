# OCI Enterprise AI Deployer

- **Author:** L. Saetta
- **Version:** 0.9.0
- **Last modified:** 2026-05-07
- **License:** MIT

![Black](https://img.shields.io/badge/code%20style-black-000000.svg)
![Pylint](https://img.shields.io/badge/pylint-10.00%2F10-brightgreen.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Pytest](https://img.shields.io/badge/tests-pytest-blue.svg)

`oci-enterprise-ai-deployer` helps operators prepare, review, and run
repeatable deployments for **OCI Enterprise AI Hosted Applications** and
**Hosted Deployments**.

The deployer starts from a declarative YAML file, renders the OCI CLI JSON
payloads, builds and pushes Docker images to OCIR, creates or reuses Hosted
Applications, and creates Hosted Deployments that point to immutable container
image tags.

Feature evolution is tracked in [FEATURE_HISTORY.md](FEATURE_HISTORY.md).

## Main Capabilities

- Web interface for loading, editing, validating, and reviewing deployment YAML
  and local `.env` files.
- Backend validation with the same strict Python schema used by the CLI.
- Dry-run review that streams the real CLI deployment plan without remote side
  effects.
- JSON artifact rendering for OCI CLI Hosted Application and Hosted Deployment
  commands.
- Container image build for `linux/amd64`.
- OCIR namespace resolution through `oci os ns get` when
  `ocir_namespace: auto` is used.
- OCIR repository creation before push when first-push repository creation is
  disabled in the tenancy.
- Docker image push to OCIR.
- Hosted Application creation or reuse by display name.
- Hosted Deployment creation from the resolved container URI and tag.
- Interactive menu for common Hosted Application and Hosted Deployment
  operations.
- Non-interactive CLI for local use and CI/CD pipelines.

## Enterprise Solution Deployment

The deployer supports two YAML shapes:

- a legacy single-deployment file
- an Enterprise Solution file with multiple deployments

An Enterprise Solution is a bounded set of deployments that share one OCI
region and one OCI compartment. These values are declared once at the top level:

```yaml
enterprise_solution:
  name: my-enterprise-ai-solution-dev
  compartment_id: ocid1.compartment.oc1..example
  region: eu-frankfurt-1
  region_key: fra
```

Use `compartment_id` when you already know the OCI compartment OCID. You can
also use `compartment_name` instead; the CLI and Web UI resolve that name
through OCI before validation, rendering, or deployment.

Each item in `deployments` defines one deployable component. Every deployment
has its own dedicated Hosted Application and one Hosted Deployment attached to
that Hosted Application.

```text
deployment[0] -> Hosted Application[0] -> Hosted Deployment[0]
deployment[1] -> Hosted Application[1] -> Hosted Deployment[1]
```

Multi-deployment execution is serial. The tool processes one deployment at a
time, in YAML order. If one deployment fails, execution stops immediately and
the output identifies the Enterprise Solution, the deployment name, and the
failed phase.

Start from the editable multi-deployment example:

```text
enterprise_ai_deployment/examples/enterprise_solution_dev.yaml
```

For a single deployable component, the legacy example remains supported:

```text
enterprise_ai_deployment/examples/agent_dev.yaml
```

## Web Interface

The web interface lives in `apps/deployer-web` and works with the FastAPI
backend in `enterprise_ai_deployment.api`.

For a complete Linux host setup with Conda, npm, firewall ports, and start
commands, see [WEB_UI_LINUX_DEPLOYMENT.md](WEB_UI_LINUX_DEPLOYMENT.md).

It is intended as a safer operational surface for deployment preparation and
review. From the browser, an operator can:

- upload or paste YAML and `.env` content
- edit configuration before running actions
- validate the YAML with backend schema and business rules
- render generated OCI CLI JSON artifacts
- run a dry-run deployment review
- start the real CLI container build action
- run the real CLI deployment action
- inspect streamed action logs and status events

The deploy action in the web interface streams the same CLI `deploy` flow used
from the terminal. A real deploy builds images, pushes them to OCIR, and creates
or reuses OCI resources.

Start the backend from the repository root. The commands below assume the
`oci-enterprise-ai-deployer` Conda environment is already active.

```bash
python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 127.0.0.1 \
  --port 8000
```

If the web console is opened from another host, expose the API host and allow
the browser origin used by the frontend:

```bash
DEPLOYER_WEB_CORS_ORIGINS=http://192.168.1.25:3000 \
python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 0.0.0.0 \
  --port 8000
```

For development on a trusted network, CORS can be opened to every browser
origin:

```bash
DEPLOYER_WEB_CORS_ORIGINS='*' \
python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 0.0.0.0 \
  --port 8000
```

Start the frontend:

```bash
cd apps/deployer-web
npm install
npm run dev
```

Then open:

```text
http://localhost:3000
```

If the backend runs elsewhere:

```bash
NEXT_PUBLIC_DEPLOYER_API_URL=http://192.168.1.25:8000 \
npm run dev -- --hostname 0.0.0.0
```

When `DEPLOYER_WEB_API_KEY` is set for the API process, start the frontend with
the same value in `NEXT_PUBLIC_DEPLOYER_API_KEY` so browser requests include the
required `X-API-Key` header.

Uploaded files do not preserve their original local path in the browser. For
web validation, relative paths in YAML are resolved from the repository root.

## Command Line Interface

The CLI is the main automation interface and the full deployment engine.

Run commands directly from the repository root:

```bash
python oci_ai_deploy.py --help
```

After editable install, console scripts are available:

```bash
oci-ai-deploy --help
```

Common commands:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  validate
```

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  render
```

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  --dry-run \
  deploy
```

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  deploy
```

Rollback to a previous immutable image tag creates a new Hosted Deployment
attached to the existing Hosted Application:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  rollback --to-tag abc1234
```

A real `deploy` runs:

```text
resolve OCIR namespace
render OCI CLI JSON artifacts
docker build
ensure OCIR repository exists
docker push
create or reuse Hosted Application
create Hosted Deployment
```

For Enterprise Solution YAML files, the same flow is applied to each deployment
in serial order.

## Interactive Menu

The project also includes a terminal menu for common manual operations:

```bash
oci-ai-deploy-menu
```

The menu focuses on Hosted Application and Hosted Deployment inspection and
creation workflows, including listing Hosted Applications, viewing details, and
building OCI CLI commands interactively.

## Requirements

Use the dedicated Conda environment:

```text
oci-enterprise-ai-deployer
```

Required local tools:

- Python 3.11 or newer in the Conda environment
- Docker Engine
- OCI CLI installed in the same environment
- Node.js and npm for the web interface
- OCI CLI profile or environment configured for the target tenancy
- Docker login to the target OCIR registry already completed

Required OCI permissions:

- read and create OCI Generative AI Hosted Applications
- read and create OCI Generative AI Hosted Deployments
- read and create target OCIR repositories
- push images to the target OCIR repository
- read Vault secrets when YAML references OCI Vault

The project expects:

```text
oci-cli==3.81.0
```

This version includes the required `generative-ai hosted-application` and
`generative-ai hosted-deployment` commands.

`auth_type: NO_AUTH` for Hosted Applications is a Limited Availability OCI
feature. The public OCI CLI/SDK model for `oci-cli==3.81.0` may not list this
mode yet, so the deployer maps declarative `NO_AUTH` to the server-side inbound
auth config type `NO_AUTH_CONFIG` when rendering OCI CLI JSON artifacts.

Check the OCI CLI from the active environment:

```bash
which oci
oci --version
oci generative-ai hosted-application --help
oci generative-ai hosted-deployment --help
```

Check OCIR namespace resolution:

```bash
oci os ns get --region eu-frankfurt-1
```

Log in to OCIR before real pushes:

```bash
docker login fra.ocir.io
```

The CLI `validate` command checks the local Docker configuration for a login
entry matching the configured OCIR registry, for example `fra.ocir.io` when
`region_key: fra` is used. If the login is missing, validation fails before
build, push, or deployment work starts.

## Quickstart

Install the Python package in editable mode:

```bash
conda activate oci-enterprise-ai-deployer
python -m pip install -r requirements.txt
python -m pip install -e .
```

Prepare local environment values:

```bash
cp enterprise_ai_deployment/examples/agent_dev.env.sample \
  enterprise_ai_deployment/examples/agent_dev.env.local
```

Use placeholders or secret references in YAML. Do not store clear-text secrets
in versioned files.

Validate the Enterprise Solution example:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  validate
```

Render JSON artifacts:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  render
```

Review the full plan without side effects:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  --dry-run \
  deploy
```

Run a real deployment only after reviewing the dry run:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/enterprise_solution_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  deploy
```

Run tests:

```bash
conda run -n oci-enterprise-ai-deployer python -m pytest
```

## Safety Notes

- `--dry-run` prints commands and renders artifacts without calling Docker push
  or OCI create operations.
- A real `deploy` builds and pushes images, then creates OCI resources.
- Existing Hosted Applications are matched by display name in the configured
  compartment.
- Hosted Applications in `DELETED` or `DELETING` lifecycle states are ignored.
- Generated artifacts are intermediate outputs and should not be treated as
  primary source files.
- Keep real `.env.local` files, OCI config, auth tokens, private keys, private
  OCIDs, and generated artifacts out of git unless intentionally shared.

### Concurrency

The deployer currently assumes that two clients do not run concurrent actions
against the same Hosted Application display name in the same OCI profile,
region, and compartment.

Hosted Application creation uses a list-then-create flow: the tool first looks
for an active Hosted Application with the configured display name and reuses it
when found; otherwise, it creates a new one. This sequence is not an atomic
lock. If two Web UI sessions or CLI processes run at the same time with the
same Hosted Application display name, both may attempt creation before either
run observes the other resource.

Avoid running overlapping deploys for the same Hosted Application until an
application-level concurrency guard or OCI-supported idempotency mechanism is
added.

## Development

Format Python code:

```bash
python -m black enterprise_ai_deployment tests
```

Run lint:

```bash
PYLINTHOME=/tmp/oci_enterprise_ai_deployer_pylint \
  python -m pylint enterprise_ai_deployment tests
```
