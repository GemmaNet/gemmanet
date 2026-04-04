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
   pip install -e ".[dev]"
   ```
4. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/my-feature
   ```

## Development Workflow

- **Run tests** before submitting:
  ```bash
  pytest tests/ -v --ignore=tests/test_e2e.py
  ```
- **Lint your code** with ruff:
  ```bash
  ruff check src/
  ruff format src/
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
