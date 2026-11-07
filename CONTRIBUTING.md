# Contributing

Benchstock is a small, early-stage project. Read the current scope and roadmap before extending it. Changes should solve a specific inventory problem and preserve the CLI's existing output contract.

1. Reproduce the behaviour with invented item data.
2. Keep business rules in the core and presentation in the CLI.
3. Add focused tests for meaningful new behaviour or a reproduced defect.
4. Run `python -m unittest discover -s tests -v` and the synthetic demo.
5. Document changes to commands, data formats or supported workflows.

Commit coherent changes when they are made; preserve their real timestamps and history. Do not claim work, tests, usage or reviews that did not happen. Do not include secrets, databases, personal records, employer or client material in contributions.

The project uses the MIT licence. Dependencies require an explicit reason and a licence review before adoption. No network service or AI API is needed for the current use case.
