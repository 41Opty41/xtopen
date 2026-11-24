# Contributing to xtopen

Thank you for helping improve `xtopen`.

## Clean-Room Policy

`xtopen` is a clean-room open-source implementation derived exclusively from public Parasolid XT documentation and synthetic test models:

- Do **not** submit proprietary `.s_t` schema files or proprietary CAD sample files in pull requests.
- Write all tests in `tests/` using synthetic builders (`tests/conftest.py`) or minimal synthetic fixtures.

## Development Setup

```bash
git clone https://github.com/41Opty41/xtopen.git
cd xtopen
python -m pip install -e ".[dev]"
pytest -v
```

With optional OpenCASCADE validation tests:

```bash
python -m pip install -e ".[dev,occt]"
pytest -v
```

## Pull Request Guidelines

- Keep runtime dependencies at **zero** (Python standard library only in core modules; keep optional CAD bindings isolated).
- Keep all source code, comments, docstrings, CLI text, and commit messages in **English**.
- Add unit tests for any new XT record, B-rep rule, or STEP entity change.
