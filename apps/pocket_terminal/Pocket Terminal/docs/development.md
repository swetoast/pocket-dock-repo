# Development

Use this guide to validate changes before creating a release.

## Source layout

```text
Pocket Terminal/
├── app/        Application modules
├── docs/       User and developer documentation
├── tests/      Regression tests
└── main.py     Application entry point
```

Keep the application cohesive. Add a module only when it represents a substantial subsystem and improves maintainability.

## Run regression tests

From the `Pocket Terminal/` directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -W error::ResourceWarning -m unittest discover -s tests -q
```

The suite covers application actions, keyboard behavior, terminal parsing, local shell execution, completion providers, security boundaries, documentation layout and package versioning.

## Check Python syntax

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile main.py app/*.py tests/test_core.py
```

Remove generated `__pycache__` directories before packaging.

## Check the launcher

From the directory containing `Pocket Terminal.sh`:

```bash
bash -n "Pocket Terminal.sh"
```

## Validate autocomplete behavior

Test both trusted and untrusted executables:

- Trusted system tools should provide dynamic options and operations.
- An executable in a writable temporary directory should appear by name but must not be executed for help discovery.
- Slow or noisy commands must be terminated at the time or output limit.
- Pending work and caches must remain within their configured bounds.

## Validate the release archive

A release is not complete until the final archive itself has been checked.

Verify that:

- `Pocket Terminal.sh` is the only file at the application root.
- Application code and documentation remain inside `Pocket Terminal/`.
- The version in `app/__init__.py` matches the release name.
- The matching changelog entry exists.
- No `__pycache__` directories or `.pyc` files are present.
- The launcher is `0755`.
- Directories are `0755`.
- Source, tests and documentation are `0644`.
- The recovery script recreates the exact release ZIP.

## Hardware verification

Container tests do not prove firmware behavior. Test device-specific items listed in [Roadmap](roadmap.md) on an RG40XX V running the target stock firmware.
