# CUTEkit
A bootstrap script that turns a fresh Ubuntu 24.04 server into a persistent, browser-accessible terminal workbench. It uses a single YAML file to describe what to install, then generates the files needed to build that environment, using APT, MISE, and curl.

## Usage

### 1. Download the files
```bash
curl -L https://github.com/joshkaspar/CUTEkit/archive/refs/heads/main.tar.gz | tar -xz
cd CUTEkit-main
```

### 2. Customize installed services and tools (optional)
Open `1-config.yaml` and uncomment or add any tools you want. The config is the single source of truth and is used to generate all other files. The config file is commented and explains most options.

Example:
```yaml
  - name: mise					# REQUIRED  human-friendly label (used in headings and messages only)
    backend: apt				# REQUIRED for apt - how the tool is installed
    package: mise				# REQUIRED for apt - exact apt package name
    installed_check: "mise ls"  # optional  bash expression; setup is skipped if it exits 0
    custom_setup: |				# optional  runs before apt-get install
      sudo install -dm 755 /etc/apt/keyrings
      curl -fsSL https://mise.jdx.dev/gpg-key.pub | sudo tee /etc/apt/keyrings/mise-archive-keyring.asc 1> /dev/null
      echo "deb [signed-by=/etc/apt/keyrings/mise-archive-keyring.asc] https://mise.jdx.dev/deb stable main" | sudo tee /etc/apt/sources.list.d/mise.list
    shell_setup:				# optional  list of lines written to .zshrc.setup
      - 'eval "$(mise activate zsh)"'
    reference:					# optional  list of background notes written to tool-reference.md
      - Installed via the official mise apt repo, not the Ubuntu default
      - Must be installed before any mise-managed tools are set up
      - 'For login shells (e.g. SSH sessions), add to ~/.zprofile: eval "$(mise activate zsh --shims)"'
```

#### Default install
- **zsh** — shell
- **zellij** — terminal multiplexer with a built-in web server
- **mise** — runtime and tool version manager (Node, Rust, and CLI tools)
- **atuin**, **fzf**, **navi**, **zoxide** — shell productivity tools
- **gh**, **lazygit**, **git-delta** — git tooling
- **glow**, **micro**, **yazi**, **bat**, **btop**, **ripgrep**, **visidata** — TUI utilities

### 3. Generate the files
```bash
python3 2-generate.py
```
This writes `3-setup.sh`, `~/.config/mise/config.toml`, `.zshrc.setup`, `4-post-install-steps.md`, and `tool-reference.md`.

### 4. Run the setup script
```bash
bash 3-setup.sh
```

### 5. Finish up
Read `4-post-install-steps.md`. It covers shell config, enabling the Zellij web terminal, setting up your reverse proxy, and locking down SSH.

## Requirements
- Ubuntu 24.04
- A non-root user with `sudo`
- Python 3 and `pyyaml` (`pip install pyyaml`) to run the generator
