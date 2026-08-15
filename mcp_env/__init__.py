"""Variable-source MCP environment generator.

Vendored and adapted from the ``build-variable-source-mcp`` skill: turns an
audited variable-sources Markdown table into a deterministic, tool-only MCP
research environment. The supplied values are authoritative; broad queries
return plausible distractors; only a fully-disambiguated query resolves the
supported value. Driven by ``xl_variable_mcp.py``.
"""
