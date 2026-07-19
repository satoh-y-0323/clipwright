# clipwright Tool Template (Scaffold)

Template source for adding new `clipwright-*` tools. Copy `clipwright-tool/`
and replace placeholders to get a working skeleton meeting CONVENTIONS MUST requirements (M1-M5):
MCP server + CLI shim + tests.

> Policy: No additional dependencies like cookiecutter (YAGNI). Simple copy + string replacement.
> Conventions source: root `CONVENTIONS.md`.

## Placeholders

| Token | Meaning | Example |
|-------|---------|---------|
| `__TOOL__` | Tool short slug (lowercase, identifier-safe) | `noise` |
| `__ACTION__` | MCP action name (snake_case verb-first) | `detect_noise` |
| `__Action__` | Action PascalCase (for class names) | `DetectNoise` |

`__TOOL__` is used in package name, file names, CLI name; `__ACTION__` in MCP tool name
`clipwright___ACTION__` and orchestration function names; `__Action__` in
options class name `__Action__Options`.

## Generation Steps (Bash / MINGW64)

```bash
# 1. Decide parameters
TOOL=noise                 # lowercase slug
ACTION=detect_noise        # snake_case action
ACTION_PASCAL=DetectNoise  # PascalCase action

# 2. Copy template to repository root
cp -r templates/clipwright-tool clipwright-$TOOL
cd clipwright-$TOOL

# 3. Replace tokens in file contents (case-sensitive, 3 tokens)
grep -rlZ -E '__ACTION__|__Action__|__TOOL__' . | xargs -0 sed -i \
  -e "s/__Action__/$ACTION_PASCAL/g" \
  -e "s/__ACTION__/$ACTION/g" \
  -e "s/__TOOL__/$TOOL/g"

# 4. Rename directories and files (directories first, then files inside)
mv src/clipwright___TOOL__ src/clipwright_$TOOL
mv src/clipwright_$TOOL/__TOOL__.py        src/clipwright_$TOOL/$TOOL.py
mv src/clipwright_$TOOL/__TOOL___cli.py    src/clipwright_$TOOL/${TOOL}_cli.py
mv tests/test___TOOL__.py                  tests/test_$TOOL.py
cd ..
```

For pure Python tools not using external OSS, `src/clipwright_$TOOL/${TOOL}_cli.py`
can be deleted (also remove `_run_cli` reference from the `*.py` main file).

## Workspace Registration

Add to root `pyproject.toml` uv workspace members:

```toml
[tool.uv.workspace]
members = ["clipwright-render", "clipwright-silence", "clipwright-transcribe", "clipwright-wrap", "clipwright-noise"]
```

Then from repository root:

```bash
uv sync
uv run --package clipwright-$TOOL pytest
uv run ruff format clipwright-$TOOL && uv run ruff check clipwright-$TOOL
uv run mypy clipwright-$TOOL/src
```

## What the Template Includes

```
clipwright-tool/
  README.md                          # Tool's own README (needs editing)
  pyproject.toml                     # MIT, clipwright dependency, ruff/mypy/pytest config
  src/clipwright___TOOL__/
    __init__.py                      # __version__
    py.typed                         # Type distribution marker
    schemas.py                       # Tool-specific Pydantic (reuse common types from clipwright.schemas)
    __TOOL__.py                      # Orchestration layer (validation→OSS→normalization→envelope)
    __TOOL___cli.py                  # OSS-wrapping subprocess CLI shim (M4, can delete if not needed)
    server.py                        # FastMCP @mcp.tool + annotations + stdio startup
  tests/
    conftest.py / test_schemas.py / test_server.py / test___TOOL__.py
```

## Post-Replacement Checklist

The template includes `TODO:` markers. At minimum, implement/verify:

- [ ] `pyproject.toml` `description`: write one sentence. If using OSS, add to dependencies.
- [ ] `schemas.py` `example_threshold`: replace with actual parameters.
- [ ] `<tool>.py` detection/analysis body (`# TODO:` block): implement.
- [ ] `server.py` annotations: adjust for detect/inspect vs render type
      (render types: `readOnlyHint=False`). If network access: `openWorldHint=True`.
- [ ] README parameter table, prerequisites (OSS PATH requirement): update.
- [ ] Path validation: delegates to `clipwright.pathpolicy`
      (`validate_source_or_basename` / `check_output_not_source`) — never
      re-implement. Media inputs routed through `inspect_media()` are already
      transitively covered.
- [ ] `CONVENTIONS.md` §7 pre-PR self-check list: pass through.
- [ ] (Optional) `evals/` AI real-task evaluation (CONVENTIONS §6).
