# Package Notes

- **Author:** L. Saetta
- **Version:** 0.1.0
- **Last modified:** 2026-05-02
- **License:** MIT

`enterprise_ai_deployment` contains the implementation for the
`oci-enterprise-ai-deployer` command line tools.

Main modules:

- `deployment_runner.py`: non-interactive YAML-driven workflow.
- `deployment_config.py`: YAML and optional `.env` loading.
- `deployment_validation.py`: local configuration validation.
- `deployment_renderer.py`: OCI CLI JSON artifact rendering.
- `cli_commands.py`: pure OCI CLI command builders.
- `ocir.py`: OCIR image URI and tag helpers.
- `menu.py`, `workflows.py`, `rendering.py`: interactive helper menu.

The root `README.md` contains installation, configuration, and operational
instructions for users.
