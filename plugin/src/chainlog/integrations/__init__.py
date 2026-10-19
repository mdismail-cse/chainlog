"""Setup / uninstall integrations. Each component builds Change objects; apply() shows nothing by
itself — the CLI prints the diff, asks, then applies. Every touched file is backed up first and
re-checked (sha) right before writing, so a concurrent edit (e.g. by herdr) is never clobbered."""
