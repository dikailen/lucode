# Python Engineering Guidance

Use this reference when the task touches Python code.

## Style and Structure

- Follow the repository's existing formatter and import style. If there is no stronger local rule, follow PEP 8.
- Use 4 spaces for indentation.
- Keep lines readable; prefer under 100 characters where practical.
- Group imports as standard library, third-party, then local modules.
- Avoid wildcard imports.
- Use `snake_case` for functions, variables, and modules; `PascalCase` for classes; `UPPER_CASE` for constants.

## Types and Data

- Add type annotations for new public functions and methods.
- Prefer Python 3.10+ union syntax when the project supports it: `str | None`.
- Prefer `dataclass`, `TypedDict`, or Pydantic models over opaque dicts when data crosses boundaries.
- Preserve existing data shapes when changing public APIs would create churn.

## Files, Paths, and Resources

- Prefer `pathlib.Path` for new path handling.
- Use context managers for files, locks, network clients, and other resources.
- Specify encodings when reading or writing text files.

## Logging and Errors

- Use `logging.getLogger(__name__)` in library/runtime code.
- Avoid `print()` in library code unless the module is explicitly CLI output.
- Catch specific exceptions. Use broad catches only at process or UI boundaries where errors are intentionally converted into user-facing messages.
- Preserve exception context with `raise ... from exc` when wrapping errors.

## Testing

- Prefer small focused tests for changed behavior.
- Use existing test framework conventions (`unittest`, `pytest`, or local harness).
- For bug fixes, add or update a test that fails before the fix when feasible.

## Common Pitfalls

- Do not introduce mutable default arguments.
- Do not change broad helper behavior without checking all call sites.
- Do not hide encoding, path, or platform assumptions.
- Do not turn a simple procedural path into a class unless the project already uses that pattern.
