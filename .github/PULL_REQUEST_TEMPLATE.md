## Description

<!-- Provide a concise summary of what changed and why. -->

## Type of Change

- [ ] Bug fix (agent/skill behavior correction)
- [ ] New skill or adapter
- [ ] Configuration change (`platform.yml` schema)
- [ ] Documentation update
- [ ] MCP server interface change

## Checklist

- [ ] I have read [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] Skill files follow the `---` YAML front-matter format (name, description)
- [ ] No secrets, account IDs, or env-specific values are hardcoded in committed files
- [ ] `platform.yml` placeholders remain as `{placeholder}` (not real values)
- [ ] Any new skill is referenced from the relevant agent (`.github/agents/*.agent.md`)
- [ ] Adapter READMEs document the required `platform.yml` overrides
- [ ] `.gitignore` updated if new artifact types were introduced

## Testing Notes

<!-- How did you verify the change? E.g., "Tested @iac-env-manager promote in a dev environment." -->

## Related Issues

<!-- Closes #123 -->
