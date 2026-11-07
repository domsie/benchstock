# Development notes

## Origin and status

Benchstock was started on 7 November 2026 as a new personal software project proposal for Dominik Siefert. The initial design, implementation and tests were developed with AI assistance. It is an original new codebase, not a fork, imported employer project or template copy.

The repository does not claim an earlier development history, habitual personal use, external users or production deployment. The original request was to explore and prepare a suitable personal software project. Whether this tool earns continued personal use should be judged by trying it, not inferred from its documentation.

Commits record the actual stages of this preparation on their real dates. They are not evidence of months of development, and their number is not a quality metric. Keep the original history during publication and subsequent work; do not backdate, squash or transplant it to make the project appear older.

## Data and dependencies

Examples and test fixtures are invented object descriptions and quantities. The intended application does not store people, customers, purchase prices, health data or account credentials. The package performs no network requests and has no runtime dependencies outside Python's standard library. Setuptools is used only for optional Python packaging.

No confidential platform instructions, employer documents or client data belong in the public repository. Local databases and generated exports are working data; do not commit private inventories. The owner must personally confirm that the project was developed using permitted personal resources and that no employer, customer, university or other entity has rights to it. Documentation is not a legal certification of those facts.

## Validation record

Validated on 7 November 2026 with Python 3.12.14 and SQLite 3.53.1 on Linux x86_64:

| Check | Result |
| --- | --- |
| `python -m unittest discover -s tests -v` | 39 tests passed, 5.082 seconds |
| `python examples/demo.py --db <fresh-temporary-path>` | Full synthetic item/kit lifecycle completed |
| `python -m compileall -q benchstock tests examples` | Passed |
| `python -m pip wheel --no-build-isolation --no-deps .` | Built the 0.1.0 wheel locally |
| Install that wheel in a fresh virtual environment using `--no-index --no-deps` | Passed |
| Run the installed `benchstock` command outside the checkout | Version, database initialisation and JSON item listing passed |

An independent read-only review also inspected the code and documentation, ran the then-current 38 tests and probed rejection of a foreign SQLite schema. One additional CLI regression test was subsequently added for a cyclic export path, bringing the final suite to 39 tests. The final test result above includes that fix.

The CI workflow is configured for Linux and Windows with Python 3.11 and 3.12. Those additional environments and hosted GitHub Actions have **not** been executed here. Local results must not be described as a successful hosted CI run. No external deployment, performance benchmark or long-term use test has been performed.

Run the test suite and synthetic demo before relying on a modified version. Proportionate verification focuses on reservation conflicts, stock invariants, transaction rollback, persistence and CLI behaviour.
