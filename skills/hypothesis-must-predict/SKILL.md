---
name: hypothesis-must-predict
version: 1
phase: hypothesize
kind: procedure
uses: []
triggers: []
applies_to_operation: ["*"]
---
## When
Every time you write a hypothesis.

## Steps
1. Write `prediction` as an observable test outcome: which failing tests will pass, and which will still fail.
2. Name the tests by their names from the failure output.
3. If the hypothesis explains only some failing tests, say which ones remain failing.
4. Make the prediction falsifiable: it must be possible to see it is wrong after the fix.

## Never
- Never write predictions like "the bug will be fixed" or "tests will pass".
- Never predict about code you have not named in `location`.
- Never keep a hypothesis whose prediction failed in the attempt log; propose a different one.

## Example
Location `shapes.py:area`. Prediction: "test_square_area and test_rect_area will pass; test_circle_area will still fail because its rounding is a separate issue."
