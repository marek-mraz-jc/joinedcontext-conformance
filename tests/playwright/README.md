# Browser journeys and accessibility (TS-12, TS-13)

`@playwright/test` against a live deployment. Journeys interact only with user-visible
controls — labels, buttons, roles — never with URL shortcuts. `@axe-core/playwright` runs on
every core view and enforces zero WCAG 2.1 AA violations.

Tasks: T-0068 (harness), T-0069 (login and onboarding), T-0070…T-0074 (journeys), T-0075 (axe).
