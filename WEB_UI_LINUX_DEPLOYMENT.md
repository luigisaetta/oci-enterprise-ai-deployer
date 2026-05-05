# Web UI Linux Deployment Guide

Author: L. Saetta  
Version: 0.1.0  
Last modified: 2026-05-05  
License: MIT

## Objective

This guide describes how to run the OCI Enterprise AI Deployer Web UI on a
Linux machine. The Web UI is composed of two local services:

- a FastAPI backend that executes validation, rendering, dry-run, build, and
  deploy operations
- a Next.js frontend used by operators from a browser

The examples below assume:

- the repository is already available on the Linux machine
- the Conda environment is activated before starting commands
- the backend API runs on port `8100`
- the Web UI runs on port `3000`
- the Linux host IP address is `192.168.1.25`

Replace `192.168.1.25` with the actual IP address or DNS name of your machine.

## Prepare The System

Install the required OS tools. On Ubuntu:

```bash
sudo apt update
sudo apt install -y git curl build-essential
```

Install Node.js 22 or newer. For example, using NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

Verify the installed versions:

```bash
node -v
npm -v
```

Install Docker and configure OCI CLI authentication according to your local
security model. Before running real deployments, the machine must also be logged
in to the target OCIR registry:

```bash
docker login fra.ocir.io
```

Use the OCIR region key that matches your deployment region.

## Create The Conda Environment

Create the dedicated environment with Python 3.11:

```bash
conda create -n oci-enterprise-ai-deployer python=3.11 -y
conda activate oci-enterprise-ai-deployer
```

All commands in the following sections assume this environment is active.

## Install Python Dependencies

From the repository root:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Verify that the expected OCI CLI is available from the active environment:

```bash
which oci
oci --version
oci generative-ai hosted-application --help
oci generative-ai hosted-deployment --help
```

The project expects `oci-cli==3.81.0`.

## Install Web UI Dependencies

From the frontend directory:

```bash
cd apps/deployer-web
npm ci
```

Use `npm ci` when `package-lock.json` is present. It installs exactly the
dependency tree recorded by the project lock file.

Optionally verify the frontend build:

```bash
npm run build
```

Return to the repository root before starting the backend:

```bash
cd ../..
```

## Open Firewall Ports

Open the Web UI and API ports with `ufw`:

```bash
sudo ufw allow 3000/tcp
sudo ufw allow 8100/tcp
sudo ufw reload
sudo ufw status
```

If the machine is hosted on OCI, also open the same ports in the relevant VCN
Security List or Network Security Group. `ufw` only controls the firewall inside
the Linux instance.

## Start The API

Start the FastAPI backend from the repository root:

```bash
DEPLOYER_WEB_API_KEY='replace-with-a-long-random-value' \
DEPLOYER_WEB_CORS_ORIGINS='*' \
python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 0.0.0.0 \
  --port 8100
```

`DEPLOYER_WEB_CORS_ORIGINS='*'` is convenient for development on a trusted
network. For a stricter setup, replace it with the frontend origin:

```bash
DEPLOYER_WEB_API_KEY='replace-with-a-long-random-value' \
DEPLOYER_WEB_CORS_ORIGINS=http://192.168.1.25:3000 \
python -m uvicorn \
  enterprise_ai_deployment.api:app \
  --host 0.0.0.0 \
  --port 8100
```

In another terminal, verify the API health endpoint:

```bash
curl http://192.168.1.25:8100/health
```

Expected response:

```json
{"status":"ok"}
```

## Start The Web Application

Open a second terminal, activate the same Conda environment if needed, and start
the frontend. Use the same API key configured for the backend:

```bash
cd apps/deployer-web
NEXT_PUBLIC_DEPLOYER_API_KEY='replace-with-a-long-random-value' \
NEXT_PUBLIC_DEPLOYER_API_URL=http://192.168.1.25:8100 \
npm run dev -- --hostname 0.0.0.0
```

The API key adds a small shared-secret check between the browser and the API.
It is useful on a trusted development network, but it is not a substitute for a
production identity provider because `NEXT_PUBLIC_` values are visible to the
browser.

Open the Web UI from a browser:

```text
http://192.168.1.25:3000
```

The browser calls the backend API at:

```text
http://192.168.1.25:8100
```

If the backend port changes, restart the frontend with an updated
`NEXT_PUBLIC_DEPLOYER_API_URL` value.

## Runtime Checklist

Before running build or deploy actions from the Web UI, verify:

- the Conda environment is active in the API terminal
- `oci --version` reports `3.81.0`
- the OCI CLI profile can access the target tenancy and region
- Docker is running
- Docker is logged in to the target OCIR registry
- ports `3000` and `8100` are reachable from the browser client
- the frontend was started with the correct `NEXT_PUBLIC_DEPLOYER_API_URL`
- the backend `DEPLOYER_WEB_API_KEY` and frontend `NEXT_PUBLIC_DEPLOYER_API_KEY`
  values match when API key protection is enabled
