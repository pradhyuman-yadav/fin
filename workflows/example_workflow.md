# Workflow: Example

Template SOP. Copy this for new workflows.

## Objective
What this workflow accomplishes, in one sentence.

## Inputs
- `input_1` — description, type, where it comes from.

## Tools
- `tools/example_tool.py` — what it does.

## Steps
1. Validate inputs.
2. Run `tools/example_tool.py` with the inputs.
3. Write intermediates to `.tmp/`.
4. Push final output to the target cloud service.

## Outputs
Where the deliverable lands (e.g. Google Sheet URL).

## Edge cases
- Rate limits: back off and retry.
- Missing input: ask the user.
