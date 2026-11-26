---
name: local-fix
version: 1
phase: operate
kind: procedure
uses: []
triggers: []
applies_to_operation: [local_fix]
---
## When
The operation is local_fix and the hypothesis names a location.

## Steps
1. read_file the location from the hypothesis, 20 lines around it.
2. Find the single line that produces the wrong value. If there is none, finish with status fail and say what you saw.
3. apply_patch: change only that line (or the smallest block). Copy `old` exactly from read_file output, with indentation, without the line numbers.
4. run_tests.
5. If the failing count went down or the failing test changed, finish with status done.
6. If nothing changed, undo your edit with apply_patch (swap old and new) and finish with status fail.

## Never
- Never edit tests/.
- Never change more than one place per apply_patch.
- Never add special cases for the exact test input.
- Never keep an edit that did not change the test result.

## Example
Hypothesis: loop stops one item early in `chunks()`. Read the loop, change `range(n - 1)` to `range(n)`, run_tests, failing count 2 -> 0, finish done.
