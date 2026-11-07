# Security

## Scope

Grain is a local voice assistant. It executes tools with the permissions of the logged-in user.
It is not a sandbox and is not a security boundary.

Python plugins, PowerShell tools, direct program actions, key-press tools, and remote tools can
have significant effects on the computer. Only install tools and plugins you trust.

## API access

The local API binds to loopback and requires an access token. Keep the tool-editor token private.
Use a separate program key for integrations. Program keys are scoped to the owning program and
cannot change settings, read history, or approve confirmations. Revoke keys that are no longer
needed.

## Reporting a vulnerability

Do not publish working credentials, tokens, private user data, or an exploitable reproduction in a
public issue. Report security issues privately to the project maintainer first. Include the Grain
version, operating system, affected endpoint or tool type, and safe reproduction steps.

## Release hygiene

Public releases must not contain user data, logs, command history, settings files, API keys,
private certificates, or build directories. Code-signing certificates and private keys must remain
in the Windows certificate store or a private signing service; only a public release signature is
distributed.
