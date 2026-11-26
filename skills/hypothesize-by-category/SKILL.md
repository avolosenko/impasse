---
name: hypothesize-by-category
version: 1
phase: hypothesize
kind: procedure
uses: []
triggers: []
applies_to_operation: ["*"]
---
## When
Every time you propose a hypothesis.

## Steps
1. From the failure, write expected vs actual.
2. Match the difference to a category:
   - one item or one unit too many/few -> boundaries (range ends, < vs <=, empty input);
   - correct first time, wrong on repeat or in another object -> state_mutation (shared defaults, caches, in-place changes);
   - wrong total, missing last element, wrong nesting -> recursion or loop logic;
   - wrong text, number format, unit, rounding -> parsing/formats;
   - allowed when it should be denied (or the reverse) -> condition logic (and/or, negation, branch order);
   - values off by a constant factor or in the wrong order between two modules -> data flow between modules.
3. If a category is assigned to you, use it even if another one looks likelier.
4. Choose `location` as file:function where the wrong value is first created, not where the test fails.
5. If the failing function only passes values through, put the location in the function that produced the value.

## Never
- Never repeat a category+location already rejected in the attempt log.
- Never name a test file as the location.

## Example
Actual (0, 0, 255), expected (255, 0, 0) for "#ff0000" -> channels in the wrong order -> data flow -> location: the function that unpacks the hex string, not the drawing code that uses it.
