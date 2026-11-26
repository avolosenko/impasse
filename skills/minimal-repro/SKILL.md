---
name: minimal-repro
version: 1
phase: operate
kind: procedure
uses: []
triggers: []
applies_to_operation: [minimal_repro]
---
## When
The operation is minimal_repro.

## Steps
1. Copy the call that fails from the test: same function, same arguments.
2. run_python with only that call and `print(repr(result))`.
3. If the output is already wrong, shrink the input: remove half of the elements or characters and run again. Keep the smaller input if it is still wrong.
4. Repeat step 3 until removing anything makes the output correct.
5. End the script with `assert result == expected, result` so it fails the same way as the test.
6. finish with `evidence`: the smallest input, expected, actual.

## Never
- Never edit source files in this operation.
- Never write a repro longer than 15 lines.
- Never guess the actual value: print it.

## Example
`merge_tags(["x","y","z"])` is wrong. Try `["x","y"]`: wrong. Try `["x"]`: correct. Smallest failing input: two tags.
