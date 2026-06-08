# Java Engineering Guidance

Use this reference when the task touches Java code.

## Project Fit First

- Follow the existing framework, package layout, validation style, mapper/service/controller boundaries, and naming conventions.
- Prefer local utility methods and existing wrappers over introducing a parallel abstraction.
- Keep changes narrow: controller changes should not silently reshape service or persistence contracts unless required.

## Spring and API Layers

- Keep request validation near DTO/input boundaries with `jakarta.validation` or `javax.validation` if the project already uses them.
- Use `@Valid` or `@Validated` at controller/service boundaries as appropriate.
- For API documentation, follow the project's existing OpenAPI/Swagger/Knife4j annotation style.
- Do not add a new configuration class when a simple `application.yml`/`application.properties` setting is enough.

## Data Mapping and Persistence

- Prefer existing mapping conventions. If the project uses builders, mappers, or `BeanUtil`, keep that style.
- Avoid long manual `setXxx()` chains when a local mapper/builder pattern is available.
- Avoid N+1 database access. Collect IDs and batch query when joining related data.
- Prefer lambda-style query/update APIs when the project already uses MyBatis-Plus or equivalent wrappers.
- Do not manually set fields that the database or ORM auto-fills unless current project behavior requires it.

## Error Handling

- Use the project's existing exception hierarchy and response shape.
- Keep business validation in service/domain logic and structural input validation in DTO/controller layers.
- Do not leak internal exception details to external API responses.

## Comments

- Add comments for non-obvious business rules, cross-field constraints, batch logic, or decisions that affect maintenance.
- Avoid comments that merely restate the code.

## Tests

- Add focused unit or integration tests according to existing project style.
- For persistence changes, verify query shape and edge cases that could trigger N+1 or missing rows.
