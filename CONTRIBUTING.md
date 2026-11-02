# Contributing

Bug reports and feature requests are welcome as GitHub issues. See [Getting help](README.md#getting-help) for other ways to reach us.

## Running the tests

Set up the virtual environment as described in the [README](README.md#installation), but install the development requirements, which add pytest to the tool's own requirements:

```bat
pip install -r requirements-dev.txt
```

Then run:

```bat
py -m pytest
```

On macOS and Linux, use `python -m pytest`.

The tests are in `tests/`. Most build small git repos in a temporary folder and need no network access.

## Network tests

The network tests in `tests/test_integration.py` sync a sample manifest (`tests/data/test-manifest.csv`) against public GitHub repos. They're skipped unless `SYNC_INTEGRATION` is set:

```bash
# macOS / Linux
SYNC_INTEGRATION=1 python -m pytest tests/test_integration.py
```

```bat
:: Windows (cmd)
set SYNC_INTEGRATION=1
py -m pytest tests\test_integration.py
```

`tests/data/sample-manifest.csv` isn't used by any test, so you can edit it freely for your own trial runs.
