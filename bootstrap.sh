#!/usr/bin/env bash
# bootstrap.sh — Ubuntu 24.04.x LTS TUI workbench bootstrap (PROD)
# Run as a normal user. Uses sudo where needed.
set -euo pipefail

# --- logging (very early) ---
LOGFILE="$HOME/bootstrap.log"
mkdir -p "$(dirname "$LOGFILE")"
exec > >(tee -a "$LOGFILE") 2>&1
trap 'rc=$?; echo "Bootstrap finished with exit code $rc at $(date -Is)"; exit $rc' EXIT

echo "Bootstrap started: $(date -Is)"
echo "Logfile: $LOGFILE"

log() { printf "\n==> %s\n" "$*"; }
ok()  { printf "    ✓ %s\n" "$*"; }
warn(){ printf "    ! %s\n" "$*"; }

if [[ $EUID -eq 0 ]]; then
  echo "Run as a normal user (not root)."
  exit 1
fi
command -v sudo >/dev/null 2>&1 || { echo "sudo is required."; exit 1; }

echo "User: $USER"
echo "Host: $(hostname)"
echo "Ubuntu: $(lsb_release -ds 2>/dev/null || true)"

# --- helper: append-only blocks in ~/.zshrc ---
touch "$HOME/.zshrc"

ensure_block() {
  local marker="$1"
  local content="$2"
  if ! grep -qF "$marker" "$HOME/.zshrc"; then
    printf "\n%s\n%s\n" "$marker" "$content" >> "$HOME/.zshrc"
    ok "Added block: $marker"
  else
    ok "Block already present: $marker"
  fi
}

# --- helper: apt noninteractive flags (avoid config prompts) ---
APT_NI=(
  DEBIAN_FRONTEND=noninteractive
  apt-get
  -y
  -o Dpkg::Options::="--force-confdef"
  -o Dpkg::Options::="--force-confold"
)

# --- 0) APT baseline ---
log "Updating apt + installing baseline packages (includes software-properties-common)"
sudo apt-get update

# Install "software-properties-common" before add-apt-repository (needed on minimal/server installs)
sudo "${APT_NI[@]}" install \
  software-properties-common

log "Enabling Ubuntu Universe"
sudo add-apt-repository -y universe

log "Upgrading base system (noninteractive)"
sudo "${APT_NI[@]}" upgrade

log "Installing baseline packages (apt)"
# Keep this list boring + reliable. No zellij here (we install via release binary).
sudo "${APT_NI[@]}" install \
  ca-certificates curl wget git unzip xz-utils jq gnupg \
  build-essential \
  openssh-server \
  qemu-guest-agent \
  zsh mc btop ripgrep

ok "Baseline apt packages installed"

# --- 1) Services: SSH + QEMU guest agent ---
log "Enabling services"
sudo systemctl enable --now ssh
sudo systemctl enable --now qemu-guest-agent
ok "ssh + qemu-guest-agent enabled"

# --- 2) SSH hardening drop-in (safe defaults; doesn't lock you out) ---
log "Writing sshd hardening drop-in (safe defaults)"
DROPIN="/etc/ssh/sshd_config.d/99-workbench-hardening.conf"
if [[ ! -f "$DROPIN" ]]; then
  sudo tee "$DROPIN" >/dev/null <<'EOF'
# Workbench SSH hardening (safe defaults)
PermitRootLogin no
X11Forwarding no
MaxAuthTries 4
ClientAliveInterval 300
ClientAliveCountMax 2

# After you've confirmed key-based login works, uncomment:
# PasswordAuthentication no
# KbdInteractiveAuthentication no
# ChallengeResponseAuthentication no
# PubkeyAuthentication yes
EOF
  sudo sshd -t
  sudo systemctl reload ssh
  ok "sshd drop-in created: $DROPIN"
else
  ok "sshd drop-in already exists (skipping)"
fi

# --- 3) Shell: set zsh as default (doesn't overwrite configs) ---
log "Setting zsh as default shell (takes effect next login)"
ZSH_PATH="$(command -v zsh)"
CURRENT_SHELL="$(getent passwd "$USER" | cut -d: -f7 || true)"
if [[ "$CURRENT_SHELL" != "$ZSH_PATH" ]]; then
  sudo chsh -s "$ZSH_PATH" "$USER"
  ok "Default shell set to zsh"
else
  ok "zsh already default"
fi

# --- 4) PATH blocks (persist + effective immediately) ---
log "Ensuring user-local paths are set"
mkdir -p "$HOME/.local/bin" "$HOME/.npm-global/bin" "$HOME/.zsh"

ensure_block "# --- workbench: user-local bin ---" \
'export PATH="$HOME/.local/bin:$PATH"'

ensure_block "# --- workbench: npm-global bin ---" \
'export PATH="$HOME/.npm-global/bin:$PATH"'

# Effective for remainder of this script:
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"

# --- 5) Add official apt repos: gh, NodeSource LTS, Charm (glow) ---
log "Adding GitHub CLI apt repo (gh)"
if [[ ! -f /etc/apt/sources.list.d/github-cli.list ]]; then
  sudo mkdir -p -m 755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null
  sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  ok "gh repo added"
else
  ok "gh repo already present"
fi

log "Adding NodeSource Node.js LTS repo (nodejs/npm)"
# NodeSource script is idempotent enough for this use; it sets up the apt source for current LTS line.
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
ok "NodeSource repo configured"

log "Adding Charm apt repo (glow)"
if [[ ! -f /etc/apt/sources.list.d/charm.list ]]; then
  sudo mkdir -p /etc/apt/keyrings
  curl -fsSL https://repo.charm.sh/apt/gpg.key \
    | sudo gpg --dearmor -o /etc/apt/keyrings/charm.gpg
  sudo chmod a+r /etc/apt/keyrings/charm.gpg
  echo "deb [signed-by=/etc/apt/keyrings/charm.gpg] https://repo.charm.sh/apt/ * *" \
    | sudo tee /etc/apt/sources.list.d/charm.list >/dev/null
  ok "Charm repo added"
else
  ok "Charm repo already present"
fi

log "Refreshing apt after adding repos"
sudo apt-get update

# --- 6) Install repo-backed tools (gh, nodejs/npm, glow) ---
log "Installing gh, nodejs, glow (apt)"
sudo "${APT_NI[@]}" install gh nodejs glow
ok "gh/node/glow installed"

# --- 7) npm: avoid sudo global installs ---
log "Configuring npm global prefix (~/.npm-global)"
npm config set prefix "$HOME/.npm-global"
ok "npm prefix set to ~/.npm-global"

# --- 8) Install/Update release-binary tools: zellij + lazygit ---
fetch_latest_version() {
  # $1 = repo "owner/name"
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" | jq -r '.tag_name' | sed 's/^v//'
}

install_release_binary_tarball() {
  # Installs a single binary from a tarball release into ~/.local/bin
  # $1 = repo "owner/name"
  # $2 = version (no leading v)
  # $3 = url template (must include {VERSION})
  # $4 = binary name inside tarball
  # $5 = target name (installed as this in ~/.local/bin)
  local repo="$1" version="$2" url_tpl="$3" bin_in="$4" target="$5"
  local url tmp

  url="${url_tpl//\{VERSION\}/$version}"
  tmp="$(mktemp -d)"
  curl -fsSL "$url" -o "$tmp/pkg.tgz"
  tar -xzf "$tmp/pkg.tgz" -C "$tmp" "$bin_in"
  install -m 755 "$tmp/$bin_in" "$HOME/.local/bin/$target"
  rm -rf "$tmp"
}

semver_lt() {
  # Returns 0 when $1 < $2, else 1
  [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" == "$1" && "$1" != "$2" ]]
}

zellij_installed_version() {
  command -v zellij >/dev/null 2>&1 || return 1
  zellij --version 2>/dev/null | awk '{print $2}' | sed 's/^v//'
}

lazygit_installed_version() {
  command -v lazygit >/dev/null 2>&1 || return 1
  lazygit --version 2>/dev/null | sed -n 's/.*version=\([^, ]*\).*/\1/p' | sed 's/^v//'
}

log "Installing/updating zellij (upstream release binary) -> ~/.local/bin/zellij"
ZELLIJ_VER="$(fetch_latest_version zellij-org/zellij)"
if ZELLIJ_CURR="$(zellij_installed_version)"; then
  if semver_lt "$ZELLIJ_CURR" "$ZELLIJ_VER"; then
    install_release_binary_tarball \
      "zellij-org/zellij" \
      "$ZELLIJ_VER" \
      "https://github.com/zellij-org/zellij/releases/download/v{VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
      "zellij" \
      "zellij"
    ok "zellij upgraded: v$ZELLIJ_CURR -> v$ZELLIJ_VER"
  else
    ok "zellij already current (v$ZELLIJ_CURR)"
  fi
else
  install_release_binary_tarball \
    "zellij-org/zellij" \
    "$ZELLIJ_VER" \
    "https://github.com/zellij-org/zellij/releases/download/v{VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
    "zellij" \
    "zellij"
  ok "zellij v$ZELLIJ_VER installed"
fi

log "Installing/updating lazygit (upstream release binary) -> ~/.local/bin/lazygit"
LAZYGIT_VER="$(fetch_latest_version jesseduffield/lazygit)"
if LAZYGIT_CURR="$(lazygit_installed_version)"; then
  if semver_lt "$LAZYGIT_CURR" "$LAZYGIT_VER"; then
    install_release_binary_tarball \
      "jesseduffield/lazygit" \
      "$LAZYGIT_VER" \
      "https://github.com/jesseduffield/lazygit/releases/download/v{VERSION}/lazygit_{VERSION}_Linux_x86_64.tar.gz" \
      "lazygit" \
      "lazygit"
    ok "lazygit upgraded: v$LAZYGIT_CURR -> v$LAZYGIT_VER"
  else
    ok "lazygit already current (v$LAZYGIT_CURR)"
  fi
else
  install_release_binary_tarball \
    "jesseduffield/lazygit" \
    "$LAZYGIT_VER" \
    "https://github.com/jesseduffield/lazygit/releases/download/v{VERSION}/lazygit_{VERSION}_Linux_x86_64.tar.gz" \
    "lazygit" \
    "lazygit"
  ok "lazygit v$LAZYGIT_VER installed"
fi

# --- 9) pyenv: deps + installer + zsh init block (with virtualenv-init) ---
log "Installing pyenv build dependencies (apt)"
sudo "${APT_NI[@]}" install \
  libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
  libncursesw5-dev libffi-dev liblzma-dev tk-dev
ok "pyenv deps installed"

log "Installing pyenv via official installer (pyenv.run) if missing"
if [[ ! -d "$HOME/.pyenv" ]]; then
  curl -fsSL https://pyenv.run | bash
  ok "pyenv installed to ~/.pyenv"
else
  ok "pyenv already present"
fi

# Guard virtualenv-init so it doesn't break shells if plugin isn't present for some reason.
ensure_block "# --- workbench: pyenv ---" \
'export PYENV_ROOT="$HOME/.pyenv"
[[ -d "$PYENV_ROOT/bin" ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - zsh)"
if [[ -d "$PYENV_ROOT/plugins/pyenv-virtualenv" ]]; then
  eval "$(pyenv virtualenv-init -)"
fi'

# --- 10) zsh-completions: clone + fpath (no forced compinit) ---
log "Installing zsh-completions (git clone) and wiring into zsh"
ZSH_COMP_DIR="$HOME/.zsh/zsh-completions"
if [[ ! -d "$ZSH_COMP_DIR" ]]; then
  git clone --depth=1 https://github.com/zsh-users/zsh-completions.git "$ZSH_COMP_DIR"
  ok "zsh-completions cloned"
else
  ok "zsh-completions already present"
fi

ensure_block "# --- workbench: zsh-completions ---" \
'fpath=("$HOME/.zsh/zsh-completions/src" $fpath)'

# --- 11) AI CLIs: Codex (npm), Claude (official installer) ---
log "Installing OpenAI Codex CLI via npm (@openai/codex)"
if ! command -v codex >/dev/null 2>&1; then
  npm install -g @openai/codex
  ok "codex installed"
else
  ok "codex already installed"
fi

log "Installing Claude Code via official installer (if missing)"
if [[ ! -x "$HOME/.local/bin/claude" ]] && ! command -v claude >/dev/null 2>&1; then
  curl -fsSL https://claude.ai/install.sh | bash
  ok "claude installed"
else
  ok "claude already installed"
fi

# --- 12) Cleanup ---
log "Cleaning up apt"
sudo "${APT_NI[@]}" autoremove
ok "apt cleanup done"

log "Bootstrap complete"
cat <<EOF

Next steps:
  1) Re-login (or run: exec zsh) to ensure default shell + PATH blocks are active.
  2) Authenticate:
       gh auth login
       codex   (or set OPENAI_API_KEY in your shell)
       claude  (follow prompts)
  3) After confirming key-based SSH works, you can harden SSH further by editing:
       $DROPIN
     and uncommenting PasswordAuthentication no, then:
       sudo systemctl reload ssh

Note:
  - zellij is installed as an upstream release binary in ~/.local/bin (no snap, no apt lag).
  - gh/node/glow are apt-managed via upstream repos (update via apt upgrade).
EOF
