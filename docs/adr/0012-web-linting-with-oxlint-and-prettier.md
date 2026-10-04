# 0012. The web package is linted by oxlint and formatted by Prettier

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-142`

## Context

The operator approved adding ESLint and Prettier to the web package, which had type checking
and nothing else. The package builds with TypeScript 7, the native compiler, which ships
without the JavaScript compiler API. typescript-eslint parses through that API and supports
TypeScript only below 6.1, so ESLint cannot read the code without a second, older TypeScript
installed beside the one that builds it.

## Decision

Lint with oxlint, which parses TypeScript itself and reads ESLint rule names and inline
`eslint-disable` comments. It runs the same small rule set the task asked for: the React hooks
rules, unused variables, no unexplained `any`, the TSDoc syntax check (the ESLint TSDoc plugin
loaded as a JavaScript plugin), and the correctness category. Format with Prettier, as
approved. Both run in `make lint`, so `make test` fails on either.

## Consequences

- One TypeScript in the package; the linter does not lag the compiler.
- Rules are ESLint's names, so moving to ESLint later, once typescript-eslint supports the
  native compiler, is a config change rather than a rewrite.
- The React Compiler rules oxlint offers (`set-state-in-effect`, `refs`, `purity`,
  `immutability`) are off: the app does not use the compiler, and meeting them means
  restructuring effects across the app. They can be turned on file by file later.
- A test lints a file that breaks each rule, so a rule that stops applying fails the suite
  rather than passing silently.

## Alternatives considered

- **ESLint with an older TypeScript installed for the parser.** Two compilers that can
  disagree about the same file, and a dependency kept only for the linter.
- **Biome for both.** One tool, but no TSDoc check and a formatter that differs from Prettier
  in small ways the operator did not ask to trade.
