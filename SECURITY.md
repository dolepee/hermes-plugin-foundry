# Execution boundary

Plugin Foundry emits code from two templates and can execute Python tests on the
host. Path confinement and a minimal child environment reduce accidental access;
they do not isolate filesystem access or networking of executed Python code.
Use trusted local inputs and inspect generated code before installation.

The plugin's path-taking tools use a Hermes approval hook for operations outside
the configured root. An approval request is not itself proof of a user's decision.
Changes to this integration must be exercised through the host's actual approval
path, including denial, cancellation and repeated calls. Unit tests invoking a
hook and then a handler do not establish those properties.

The CLI is a separate local operator surface. It can install generated plugins;
model-facing Foundry tools do not offer an installation option. Test subprocess
environment filtering does not prevent malicious code reading files accessible
to the same OS user.

Report vulnerabilities privately to the repository owner through GitHub's private
security reporting when available. Avoid putting credentials, private filesystem
data or exploitable details involving another project's boundary in public issues.
