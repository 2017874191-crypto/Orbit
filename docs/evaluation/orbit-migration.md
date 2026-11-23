# Orbit namespace migration

The Python package, CLI, environment variables, plugin entry points, state
directories, and benchmark namespace now use the Orbit identity.

| Surface | Current identity |
| --- | --- |
| Product | Orbit |
| Distribution | `orbit-harness` |
| Python imports | `orbit` |
| CLI | `orbit` |
| Configuration module | `orbit.config.orbit` |
| Environment prefix | `ORBIT_` |
| Default state directory | `~/.orbit` |
| Plugin entry point group | `orbit.plugins` |
| Benchmark package | `benchmarks.orbitbench` |

Install locally with `uv tool install --editable .`. For the native TUI,
run `npm ci --prefix ui-tui` followed by `npm run build --prefix ui-tui`.
Restart existing Python processes after reinstalling. Third-party plugins
must update their imports, entry points, manifest names, and configuration
references to match the new namespace.

The application reads configuration from its new state directory. Existing
local configuration can be copied there without committing credentials.
Existing state is not automatically merged or deleted.

Validation on Windows: 198 product/configuration/session/scheduler tests and
86 TUI session RPC tests passed. The native TUI bundle built successfully,
and `orbit --version` reported `Orbit v0.1.7`.
Context and Tool/MCP benchmark test collection stopped because their artifact
module imports the Unix-only `fcntl` module; these benchmark tests remain
unverified on Windows. No live model evaluation was performed.

Original licenses and attribution notices are retained. Renaming the project
does not change the origin or authorship of the upstream implementation.
