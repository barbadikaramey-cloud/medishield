# MediShield Engineering Rules

1. Do not hardcode domain data, catalogs, word lists, synonyms, thresholds, or business rules in application code. All terminology, catalogs, configurations, and reference data must be dynamically managed via relational persistence, migrations, configuration, or semantic model inference.

2. Canonical specifications are the source of truth.

3. Do not modify architecture without an ADR.

4. Before implementing a feature:
   - identify the source requirement
   - explain the feature
   - explain why it exists
   - identify affected components
   - define acceptance criteria

5. Implement only the requested feature.

6. Do not silently introduce dependencies.

7. Every production feature requires tests.

8. Every API change requires API contract updates.

9. Every database change requires a migration.

10. Every workflow change requires the LangGraph/workflow specification update.

11. Do not put business rules inside prompts when they belong in code.

12. Do not use an LLM when deterministic logic is sufficient.

13. Preserve evidence and provenance.

14. Never silently convert uncertainty into failure.

15. Never let the LLM directly determine the final business decision.

16. After implementation:
    - run tests
    - run lint/type checks
    - summarize changed files
    - explain important implementation decisions
    - identify remaining risks
    - stop