---
name: revert-and-narrow
version: 1
phase: operate
kind: procedure
uses: []
triggers: [same_error_x3, repeat_diff, no_progress]
applies_to_operation: [revert]
---
## When
The same error repeats, edits are repeated, or several iterations did not reduce failing tests.

## Steps
1. Read the attempt log. List the locations already edited.
2. Undo every earlier edit that did not reduce the failing count (apply_patch back to the original text).
3. run_tests and confirm the failure is the original one.
4. Choose a location NOT in the list from step 1: the next frame up in the traceback, or the function that creates the input of the failing call.
5. Check that location with run_python: call it with the test input and print its result.
6. finish with `evidence` naming the new location and the printed value.

## Never
- Never retry an edit that is already in the attempt log.
- Never edit a new location before confirming with run_python that its output is wrong.

## Example
Three edits in `render_badge()` did not help. Revert them. Its input comes from `load_profile()`. run_python shows `load_profile()` returns the age as the string "42", not the number 42 -> new location `load_profile()`.
