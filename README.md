# cutekit

Bootstrap script for building an Ubuntu 24.04.x “TUI workbench” with an SSH-first workflow, multiplexer UX, and predictable user-local installs.

## TODO

### Generator rewrite (`2-generate.py`)
- [x] M1: Load and validate `1-config.yaml`, print tool summary
- [x] M2: Generate `~/.config/mise/config.toml` from mise-backend tools
- [x] M3: Generate `3-setup.sh` (apt installs + curl manual installers)
- [x] M4: Generate `.zshrc.setup` from `shell_setup` fields
- [x] M5: Generate `4-post-install-steps.md` and `tool-reference.md`

### Tools and environment
- [x] Break post-bootstrap output into `do-this-first.md` (SSH hardening, zellij web setup) and `app-reference.md` (per-tool auth/setup steps); print a message at end of script explaining what each file is for
- [ ] Add a 'real' editor like Helix or Micro
- [ ] Add chezmoi
- [ ] move environment to VPS
- [ ] implement cert and reverse proxy (caddy)
- [x] Add popular TUI apps: fzf atuin zoxid bat delta navi visidata
- [x] Add steps to make zsh default
- [x] Add steps to start zellij by defalault
- [x] Move browser terminal access from cloudflare to zellij

## Later
- [ ] Add architecture detection and support logic (e.g., x86_64 vs arm64) for upstream binary downloads and related install paths.
- [ ] Add setup instructions for raspberry pi

