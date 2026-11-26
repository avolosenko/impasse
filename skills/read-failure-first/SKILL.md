---
name: read-failure-first
version: 1
phase: operate
kind: procedure
uses: []
triggers: []
applies_to_operation: [read_more_code, minimal_repro, add_logging, bisect]
---
## When
Before reading or running anything else in a diagnostic operation.

## Steps
1. Read the last line of the traceback: exception type and message, or the assert with expected and actual values.
2. Write down in `thought`: which test failed, expected value, actual value.
3. Walk the traceback from the bottom up. List every frame that is in a source file (not tests/, not the standard library).
4. read_file the innermost source frame first, 20 lines around the reported line.
5. If that code receives a wrong input, read_file the caller that produced the input (next frame up).
6. Stop reading when you find the first line where a value becomes wrong. Report it in `evidence` as file:line, expected vs actual.

## Never
- Never start with files that do not appear in the traceback.
- Never read a whole file of more than 80 lines at once; give start and end.
- Never report a location without the expected and actual value.

## Example
Assert `['ann', 'bo'] == ['ann', 'bo', 'cy']` in a test of `top_scores()`: expected 3 names, got 2 -> the last one is missing -> read the loop in `top_scores()` that builds the list.
