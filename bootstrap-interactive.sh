#!/usr/bin/env bash
# bootstrap-interactive.sh — Ubuntu 24.04.x LTS TUI workbench bootstrap (INTERACTIVE)
# Run as a normal user. Uses sudo where needed.
set -euo pipefail

STATE_FILE="${BOOTSTRAP_STATE_FILE:-$HOME/.bootstrap-interactive-state}"
LOGFILE="${BOOTSTRAP_LOGFILE:-$HOME/bootstrap-interactive.log}"

mkdir -p "$(dirname "$LOGFILE")"
exec > >(tee -a "$LOGFILE") 2>&1
trap 'rc=$?; echo "Bootstrap finished with exit code $rc at $(date -Is)"; exit $rc' EXIT

echo "Bootstrap started: $(date -Is)"
echo "Mode: interactive"
echo "Logfile: $LOGFILE"
echo "State file: $STATE_FILE"

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

APT_NI=(
  DEBIAN_FRONTEND=noninteractive
  apt-get
  -y
  -o Dpkg::Options::="--force-confdef"
  -o Dpkg::Options::="--force-confold"
)

touch "$STATE_FILE"
LAST_STEP="$(cat "$STATE_FILE")"

run_step() {
  local step_name="$1"
  shift

  if [[ -n "$LAST_STEP" ]]; then
    if [[ "$LAST_STEP" == "$step_name" ]]; then
      LAST_STEP=""
      ok "Resuming after completed step: $step_name"
      return 0
    fi
    warn "Skipping already completed step: $step_name"
    return 0
  fi

  log "$step_name"
  set -x
  "$@"
  { set +x; } 2>/dev/null

  printf '%s\n' "$step_name" > "$STATE_FILE"
  ok "Checkpoint saved: $step_name"
}

step_apt_baseline() {
  sudo apt-get update
  sudo "${APT_NI[@]}" install software-properties-common
  sudo add-apt-repository -y universe
  sudo "${APT_NI[@]}" upgrade
  sudo "${APT_NI[@]}" install \
    ca-certificates curl wget git unzip xz-utils jq gnupg \
    build-essential \
    openssh-server \
    qemu-guest-agent \
    zsh mc btop ripgrep
  ok "Baseline apt packages installed"
}

step_services() {
  sudo systemctl enable --now ssh
  sudo systemctl enable --now qemu-guest-agent
  ok "ssh + qemu-guest-agent enabled"
}

step_ssh_hardening() {
  DROPIN="/etc/ssh/sshd_config.d/99-workbench-hardening.conf"
  if [[ ! -f "$DROPIN" ]]; then
    sudo tee "$DROPIN" >/dev/null <<'EOC'
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
EOC
    sudo sshd -t
    sudo systemctl reload ssh
    ok "sshd drop-in created: $DROPIN"
  else
    ok "sshd drop-in already exists (skipping)"
  fi
}

step_shell_setup() {
  ZSH_PATH="$(command -v zsh)"
  CURRENT_SHELL="$(getent passwd "$USER" | cut -d: -f7 || true)"
  if [[ "$CURRENT_SHELL" != "$ZSH_PATH" ]]; then
    sudo chsh -s "$ZSH_PATH" "$USER"
    ok "Default shell set to zsh"
  else
    ok "zsh already default"
  fi
}

step_path_setup() {
  mkdir -p "$HOME/.local/bin" "$HOME/.npm-global/bin" "$HOME/.zsh"
  ensure_block "# --- workbench: user-local bin ---" \
'export PATH="$HOME/.local/bin:$PATH"'
  ensure_block "# --- workbench: npm-global bin ---" \
'export PATH="$HOME/.npm-global/bin:$PATH"'
  export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
}

step_add_repos() {
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

  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  ok "NodeSource repo configured"

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

  sudo apt-get update
}

step_install_repo_tools() {
  sudo "${APT_NI[@]}" install gh nodejs glow
  ok "gh/node/glow installed"
}

step_npm_prefix() {
  npm config set prefix "$HOME/.npm-global"
  ok "npm prefix set to ~/.npm-global"
}

fetch_latest_version() {
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" | jq -r '.tag_name' | sed 's/^v//'
}

install_release_binary_tarball() {
  local repo="$1" version="$2" url_tpl="$3" bin_in="$4" target="$5"
  local url tmp

  url="${url_tpl//\{VERSION\}/$version}"
  tmp="$(mktemp -d)"
  curl -fsSL "$url" -o "$tmp/pkg.tgz"
  tar -xzf "$tmp/pkg.tgz" -C "$tmp" "$bin_in"
  install -m 755 "$tmp/$bin_in" "$HOME/.local/bin/$target"
  rm -rf "$tmp"
}

step_release_tools() {
  if command -v zellij >/dev/null 2>&1; then
    ok "zellij already present (will be updated via maintenance)"
  else
    ZELLIJ_VER="$(fetch_latest_version zellij-org/zellij)"
    install_release_binary_tarball \
      "zellij-org/zellij" \
      "$ZELLIJ_VER" \
      "https://github.com/zellij-org/zellij/releases/download/v{VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
      "zellij" \
      "zellij"
    ok "zellij v$ZELLIJ_VER installed"
  fi

  if command -v lazygit >/dev/null 2>&1; then
    ok "lazygit already present (will be updated via maintenance)"
  else
    LAZYGIT_VER="$(fetch_latest_version jesseduffield/lazygit)"
    install_release_binary_tarball \
      "jesseduffield/lazygit" \
      "$LAZYGIT_VER" \
      "https://github.com/jesseduffield/lazygit/releases/download/v{VERSION}/lazygit_{VERSION}_Linux_x86_64.tar.gz" \
      "lazygit" \
      "lazygit"
    ok "lazygit v$LAZYGIT_VER installed"
  fi
}

step_pyenv() {
  sudo "${APT_NI[@]}" install \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libncursesw5-dev libffi-dev liblzma-dev tk-dev
  ok "pyenv deps installed"

  if [[ ! -d "$HOME/.pyenv" ]]; then
    curl -fsSL https://pyenv.run | bash
    ok "pyenv installed to ~/.pyenv"
  else
    ok "pyenv already present"
  fi

  ensure_block "# --- workbench: pyenv ---" \
'export PYENV_ROOT="$HOME/.pyenv"
[[ -d "$PYENV_ROOT/bin" ]] && export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init - zsh)"
if [[ -d "$PYENV_ROOT/plugins/pyenv-virtualenv" ]]; then
  eval "$(pyenv virtualenv-init -)"
fi'
}

step_zsh_completions() {
  ZSH_COMP_DIR="$HOME/.zsh/zsh-completions"
  if [[ ! -d "$ZSH_COMP_DIR" ]]; then
    git clone --depth=1 https://github.com/zsh-users/zsh-completions.git "$ZSH_COMP_DIR"
    ok "zsh-completions cloned"
  else
    ok "zsh-completions already present"
  fi

  ensure_block "# --- workbench: zsh-completions ---" \
'fpath=("$HOME/.zsh/zsh-completions/src" $fpath)'
}

step_ai_clis() {
  if ! command -v codex >/dev/null 2>&1; then
    npm install -g @openai/codex
    ok "codex installed"
  else
    ok "codex already installed"
  fi

  if [[ ! -x "$HOME/.local/bin/claude" ]] && ! command -v claude >/dev/null 2>&1; then
    curl -fsSL https://claude.ai/install.sh | bash
    ok "claude installed"
  else
    ok "claude already installed"
  fi
}

step_cleanup() {
  sudo "${APT_NI[@]}" autoremove
  ok "apt cleanup done"
}

run_step "0) APT baseline" step_apt_baseline
run_step "1) Services: SSH + QEMU guest agent" step_services
run_step "2) SSH hardening drop-in" step_ssh_hardening
run_step "3) Shell setup" step_shell_setup
run_step "4) PATH blocks" step_path_setup
run_step "5) Add official apt repos" step_add_repos
run_step "6) Install repo-backed tools" step_install_repo_tools
run_step "7) npm prefix setup" step_npm_prefix
run_step "8) Install/Update release-binary tools" step_release_tools
run_step "9) pyenv setup" step_pyenv
run_step "10) zsh-completions" step_zsh_completions
run_step "11) AI CLIs" step_ai_clis
run_step "12) Cleanup" step_cleanup

log "Bootstrap complete"
cat <<EOF2

Next steps:
  1) Re-login (or run: exec zsh) to ensure default shell + PATH blocks are active.
  2) Authenticate:
       gh auth login
       codex   (or set OPENAI_API_KEY in your shell)
       claude  (follow prompts)
  3) After confirming key-based SSH works, you can harden SSH further by editing:
       /etc/ssh/sshd_config.d/99-workbench-hardening.conf
     and uncommenting PasswordAuthentication no, then:
       sudo systemctl reload ssh

Note:
  - zellij is installed as an upstream release binary in ~/.local/bin (no snap, no apt lag).
  - gh/node/glow are apt-managed via upstream repos (update via apt upgrade).
EOF2
