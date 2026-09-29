# Parity-Loop Porting Notes

Reference notes for the parity loop's Phase 2 plan (`skill-body.md`): measured
behavior of external tools that Pi shells out to and pipy emulates. Pin each
rule that applies in the plan before review.

## Emulating rg and fd

Measure the real binary before writing the plan: build a scratch tree and drive
the binary from a small subprocess script. The TOOLS1 plan review took four
rounds, and each round found one rule the plan had not pinned. Rules measured so
far:

- `fd` matches basenames at any depth, uses smart case, prints a trailing slash
  on directories, and treats `--max-results 0` as unlimited.
- `rg --glob` is case-sensitive. A glob that contains a slash is relative to
  rg's cwd, not to the search path. A leading `!` negates the glob and prunes
  the matching directories. An explicit file path ignores the glob.

## Resource guarantees of a Python stand-in

A Python fallback for a native tool must keep the tool's resource guarantees,
not only its output. The TOOLS1 reviewer flagged whole-file reads with no size
cap, and an in-process `re` search that could not stop a backtracking pattern.
Run the fallback as a child process that streams the same JSON as the real
binary. One reader then handles both engines, kills the child at the result
limit, and kills it on `cancel_event`. Read files line by line.
