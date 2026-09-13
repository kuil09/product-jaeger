# Contributing

Run `pip install ".[dev]"`, `pytest`, `ruff check .`, and `mypy src` before a pull request. Adapters require fixture tests and must not require credentials in pull-request workflows. Public output must use the allowlisted projection in `product_jaeger.digest`.
