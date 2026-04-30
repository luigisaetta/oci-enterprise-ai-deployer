# OCI Enterprise AI Deployer Agent Instructions

Author: L. Saetta
Version: 0.1.0
Last modified: 2026-04-30
License: MIT

## Scope

These instructions apply to the whole repository.

## Required Project Specifications

All development work in this repository must conform to the project
specifications below:

- `enterprise_ai_deployment/design_deploy_oci_enterprise_ai.md`
- `enterprise_ai_deployment/implementation_spec_codex.md`

When implementation choices are ambiguous, treat those documents as the source
of truth. If the code and the specifications diverge, either update the code to
match the specifications or explicitly update the relevant specification in the
same change.

## Development Guidelines

- Keep the tool focused on automated deployment of OCI Enterprise AI Hosted
  Applications and Hosted Deployments.
- Always use the dedicated Conda environment `oci-enterprise-ai-deployer` for
  tests and local validation commands. Run test commands through
  `conda run -n oci-enterprise-ai-deployer ...` unless the environment is
  already active in the current shell.
- Preserve the declared version 1 constraints unless the specifications are
  intentionally revised.
- Prefer small, testable Python modules over large command-oriented scripts.
- Keep generated OCI CLI JSON artifacts as intermediate outputs, not as primary
  source files.
- Do not print secrets or clear-text secret values in logs, dry-run output, or
  tests.
- Keep `dry-run` behavior accurate for every operation that can create, update,
  push, or deploy resources.
- Add or update tests for behavior changes that affect validation, rendering,
  command construction, or deployment orchestration.

## File Headers and Versioning

Every source, documentation, configuration, and example file should include a
short header with version metadata when the file format allows it. Use the
existing local style for the file type and include at least:

- author
- version
- last modified date
- license, when appropriate

Use the package version from `pyproject.toml` unless a document has its own
intentional version, such as a design specification.
