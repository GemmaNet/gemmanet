# Contributing to GemmaNet

Thank you for your interest in contributing to GemmaNet! We welcome
contributions of all kinds: bug fixes, features, documentation, and more.

## Getting Started

1. **Fork** the repository on GitHub
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/gemmanet.git
   cd gemmanet
   ```
3. **Set up** a development environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install --require-hashes -r requirements/dev.txt
   pip install --no-deps --no-build-isolation -e .
   ```
4. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/my-feature
   ```

## Dependencies

Python dependencies are hash-locked: `requirements/app.txt` (coordinator
image), `requirements/dev.txt` (development and CI) and `docs/requirements.txt`
(docs), generated from `pyproject.toml` and `docs/requirements.in`. After
changing a dependency, regenerate them with Python 3.11:

```bash
pip install pip-tools
scripts/lock.sh
```

Base images are pinned by digest; Dependabot proposes updates weekly, and
CI (including a Trivy scan of every image) checks each one.

## Development Workflow

- **Run tests** before submitting. They need PostgreSQL and Redis (see
  `.github/workflows/test.yml` for the exact setup):
  ```bash
  export DATABASE_URL=postgresql://gemmanet:gemmanet@localhost:5432/gemmanet_test
  pytest tests/ -v
  ```
- **Lint your code** with ruff:
  ```bash
  ruff check src/ tests/
  ```
- Keep commits focused and write clear commit messages.

## Submitting a Pull Request

1. Push your branch to your fork
2. Open a pull request against `main`
3. Describe what your PR does and why
4. Ensure all CI checks pass

## Code of Conduct

Be respectful and constructive. We are building an open community and
expect all participants to act professionally.

## Questions?

Open an issue on GitHub or start a discussion. We're happy to help!
