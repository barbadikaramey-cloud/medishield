# MediShield Engineering Rules

1. Canonical specifications are the source of truth.

2. Do not modify architecture without an ADR.

3. Before implementing a feature:
   - identify the source requirement
   - explain the feature
   - explain why it exists
   - identify affected components
   - define acceptance criteria

4. Implement only the requested feature.

5. Do not silently introduce dependencies.

6. Every production feature requires tests.

7. Every API change requires API contract updates.

8. Every database change requires a migration.

9. Every workflow change requires the LangGraph/workflow specification update.

10. Do not put business rules inside prompts when they belong in code.

11. Do not use an LLM when deterministic logic is sufficient.

12. Preserve evidence and provenance.

13. Never silently convert uncertainty into failure.

14. Never let the LLM directly determine the final business decision.

15. After implementation:
    - run tests
    - run lint/type checks
    - summarize changed files
    - explain important implementation decisions
    - identify remaining risks
    - stop