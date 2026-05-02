# OCI Enterprise AI Deployer

- **Author:** L. Saetta
- **Version:** 0.1.0
- **Last modified:** 2026-05-02
- **License:** MIT

![Black](https://img.shields.io/badge/code%20style-black-000000.svg)
![Pylint](https://img.shields.io/badge/pylint-10.00%2F10-brightgreen.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Pytest](https://img.shields.io/badge/tests-pytest-blue.svg)

`oci-enterprise-ai-deployer` is a small Python CLI for building container images
and deploying **OCI Enterprise AI Hosted Applications** and **Hosted Deployments** 
from a declarative YAML file.

The project is intended for **repeatable agent deployment workflows** where the
operator wants to review generated OCI CLI commands, render complex JSON
payloads, build and publish a Docker image to OCIR, reuse an existing Hosted
Application when possible, and create a Hosted Deployment that points to the
published image.

Feature evolution is tracked in [FEATURE_HISTORY.md](FEATURE_HISTORY.md).

## What It Does

- Loads deployment settings from YAML.
- Renders OCI CLI JSON artifacts for Hosted Application and Hosted Deployment.
- Builds a Docker image for `linux/amd64`.
- Resolves `ocir_namespace: auto` through `oci os ns get`.
- Pushes the tagged image to OCIR.
- Creates or reuses a Hosted Application by display name.
- Ignores deleted Hosted Applications during reuse checks.
- Creates a Hosted Deployment from the Docker image URI and tag.
- Supports dry-run mode for command review without side effects.

## Repository Layout

```text
.
├── enterprise_ai_deployment/      # CLI package
│   ├── deployment_runner.py       # non-interactive deploy workflow
│   ├── deployment_config.py       # YAML loading
│   ├── deployment_renderer.py     # JSON artifact rendering
│   ├── cli_commands.py            # OCI CLI command builders
│   ├── workflows.py               # interactive menu workflows
│   └── examples/agent_dev.yaml    # editable deployment template
├── apps/deployer-web              # Next.js UI preview
├── examples/hello_world_container # minimal container target for local tests
├── tests/                         # pytest suite
├── oci_ai_deploy.py               # repository-root CLI wrapper
└── pyproject.toml                 # package and tool configuration
```

## Prerequisites

- Conda environment named `oci-enterprise-ai-deployer`.
- Python 3.11 or newer in that environment.
- Docker Engine.
- OCI CLI installed and configured.
- Permission to read/create OCI Generative AI Hosted Applications and Hosted
  Deployments.
- Permission to push images to the target OCIR repository.
- Docker login to OCIR, for example:

```bash
docker login fra.ocir.io
```

The OCI CLI must be able to resolve the namespace when the YAML uses:

```yaml
container:
  ocir_namespace: auto
```

Check it with:

```bash
oci os ns get --region eu-frankfurt-1
```

## Quickstart

From the repository root:

```bash
conda activate oci-enterprise-ai-deployer
python -m pip install -r requirements.txt
python -m pip install -e .
```

Required Python dependencies are listed in `requirements.txt`:

- `fastapi`
- `oci-cli==3.81.0`
- `pydantic`
- `python-dotenv`
- `PyYAML`
- `rich`
- `uvicorn`
- `black`
- `httpx`
- `pylint`
- `pytest`
- `pytest-cov`

`oci-cli==3.81.0` installs the compatible OCI Python SDK dependency
`oci==2.173.0` and includes the Hosted Application and Hosted Deployment
commands used by this project.

External tools required for real deployments:

- Docker Engine
- OCI CLI profile or environment configured for the target tenancy
- OCIR login for the target region

Confirm that the Conda environment CLI is the one being used:

```bash
which oci
oci --version
oci generative-ai hosted-application --help
oci generative-ai hosted-deployment --help
```

The expected CLI version is `3.81.0`. If `which oci` points outside the Conda
environment, activate `oci-enterprise-ai-deployer` again or call
`$CONDA_PREFIX/bin/oci` explicitly.

Run the test suite with the dedicated Conda environment:

```bash
conda run -n oci-enterprise-ai-deployer python -m pytest
```

You can run the CLI directly without installing:

```bash
python oci_ai_deploy.py --help
```

After editable install, the console scripts are also available:

```bash
oci-ai-deploy --help
oci-ai-deploy-menu
```

## Web UI Preview

The repository includes a Next.js interface prototype in `apps/deployer-web`.
It currently implements a UI-only workflow for uploading, viewing, editing, and
previewing YAML and `.env` deployment files. The backend performs real YAML and
configuration validation with the same Python rules used by the CLI, including
a strict Pydantic schema that rejects unknown YAML fields. The `Review dry run`
action invokes the real Python CLI with `--dry-run deploy` and streams its
output back to the UI. The `Render JSON artifacts` action also invokes the real
Python CLI and streams the generated artifact paths. The `Build container image`
action invokes the real Python CLI `build` command and stops before any OCIR
push. The full `Deploy` action still streams preview events and does not call
Docker or OCI yet.

Start the FastAPI backend from the repository root:

```bash
conda run -n oci-enterprise-ai-deployer python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 127.0.0.1 \
  --port 8000
```

The API listens on:

```text
http://localhost:8000
```

From the web app directory:

```bash
npm install
npm run dev
```

Then open:

```text
http://localhost:3000
```

If the backend runs on a different URL, start Next.js with:

```bash
NEXT_PUBLIC_DEPLOYER_API_URL=http://localhost:8000 npm run dev
```

The `dev` and `build` scripts clean the local `.next` directory before starting.
If the CSS ever appears stale during local development, stop the dev server and
start it again with `npm run dev`. Do not run `npm run build` while a dev server
for the same app is still running.

Uploaded files do not preserve their original local path in the browser. For web
validation, relative paths in the YAML are resolved from the repository root.

## Configure A Deployment

Start from:

```text
enterprise_ai_deployment/examples/agent_dev.yaml
```

Set at least:

- `application.compartment_id`
- `application.region`
- `application.region_key`
- `container.context`
- `container.dockerfile`
- `container.repository`
- `container.image_name`
- `hosted_application.display_name`
- `hosted_application.security`
- `hosted_deployment.display_name`

For local secret references, copy the sample env file and keep the real one out
of git:

```bash
cp enterprise_ai_deployment/examples/agent_dev.env.sample \
  enterprise_ai_deployment/examples/agent_dev.env.local
```

## Common Commands

Validate the YAML:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/agent_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  validate
```

Render generated JSON artifacts:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/agent_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  render
```

Review the full deployment flow without side effects:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/agent_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  --dry-run \
  deploy
```

Run the full flow:

```bash
python oci_ai_deploy.py \
  --config enterprise_ai_deployment/examples/agent_dev.yaml \
  --env-file enterprise_ai_deployment/examples/agent_dev.env.local \
  deploy
```

The full flow currently runs:

```text
resolve OCIR namespace
docker build
docker push
create or reuse Hosted Application
create Hosted Deployment
```

## Safety Notes

- `--dry-run` prints commands and renders artifacts without calling OCI or
  Docker.
- A real `deploy` builds and pushes the image, then creates OCI resources.
- Existing Hosted Applications are matched by display name in the configured
  compartment.
- Hosted Applications in `DELETED` or `DELETING` lifecycle states are ignored.
- Keep `.env.local`, OCI config, auth tokens, private keys, OCIDs for private
  tenancies, and generated artifacts out of git unless intentionally shared.

## Development

Run tests:

```bash
python -m pytest -q
```

Run formatting:

```bash
python -m black enterprise_ai_deployment tests
```

Run lint:

```bash
PYLINTHOME=/tmp/oci_enterprise_ai_deployer_pylint \
  python -m pylint enterprise_ai_deployment tests
```
