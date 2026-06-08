# C++ Engineering Guidance

Use this reference when the task touches C++ code.

## Baseline

- Follow the project's existing C++ standard, compiler flags, naming style, and build system.
- Prefer modern C++17/20 practices only when the project supports them.
- Keep ABI/public header changes narrow and intentional.

## Resource Management

- Use RAII for owned resources: memory, file handles, locks, sockets, and native handles.
- Avoid raw `new`/`delete` and `malloc`/`free` in new code.
- Prefer `std::unique_ptr` for ownership; use `std::shared_ptr` only when ownership is genuinely shared.
- Treat raw pointers as non-owning observers unless clearly documented otherwise.
- Wrap C API resources immediately in an owning type or custom deleter.

## Const and Type Safety

- Prefer `const` for values and member functions that do not mutate state.
- Pass large read-only objects by `const&`.
- Use `nullptr` instead of `NULL` or `0`.
- Use `enum class` instead of unscoped enums in new code.
- Prefer `std::optional`, `std::variant`, `std::string_view`, and `std::span` when supported and appropriate.

## Function and API Design

- Keep functions short and single-purpose.
- Avoid long parameter lists; consider a small struct when parameters form a concept.
- Prefer return values over output parameters where this fits existing style.
- Preserve exception/noexcept conventions used by the project.

## Build and Tests

- Run the narrowest relevant build or test target.
- For template or header changes, check downstream compile impact.
- For ownership changes, consider sanitizer or leak checks when available.
