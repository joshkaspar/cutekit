#!/usr/bin/env bash
# maintenance.sh — weekly updates for the workbench
set -euo pipefail

LOGFILE="$HOME/maintenance.log"
mkdir -p "$(dirname "$LOGFILE")"
exec > >(tee -a "$LOGFILE") 2>&1
trap 'rc=$?; echo "Maintenance finished with exit code $rc at $(date -Is)"; exit $rc' EXIT

echo "Maintenance started: $(date -Is)"
echo "Logfile: $LOGFILE"

log() { printf "\n==> %s\n" "$*"; }
ok()  { printf "    ✓ %s\n" "$*"; }
warn(){ printf "    ! %s\n" "$*"; }

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
export DEBIAN_FRONTEND=noninteractive

fetch_latest_version() {
  curl -fsSL "https://api.github.com/repos/$1/releases/latest" | jq -r '.tag_name' | sed 's/^v//'
}

install_release_binary_tarball() {
  local version="$1" url_tpl="$2" bin_in="$3" target="$4"
  local url tmp
  url="${url_tpl//\{VERSION\}/$version}"
  tmp="$(mktemp -d)"
  curl -fsSL "$url" -o "$tmp/pkg.tgz"
  tar -xzf "$tmp/pkg.tgz" -C "$tmp" "$bin_in"
  install -m 755 "$tmp/$bin_in" "$HOME/.local/bin/$target"
  rm -rf "$tmp"
}

log "APT: update/upgrade"
sudo apt-get update -y
sudo apt-get upgrade -y
sudo apt-get autoremove -y
ok "apt updated"

log "Codex: update via npm (with before/after version check)"
if command -v npm >/dev/null 2>&1; then
  before="$(npm list -g --depth=0 @openai/codex 2>/dev/null | awk -F@ '/@openai\/codex@/ {print $NF}')"
  before="${before:-none}"

  npm install -g @openai/codex@latest

  after="$(npm list -g --depth=0 @openai/codex 2>/dev/null | awk -F@ '/@openai\/codex@/ {print $NF}')"
  after="${after:-unknown}"

  if [[ "$before" == "$after" ]]; then
    ok "codex already up to date ($after)"
  else
    ok "codex updated: $before → $after"
  fi
else
  warn "npm not found; skipping codex update"
fi

log "zellij: update if newer release exists"
if command -v zellij >/dev/null 2>&1; then
  current="$(zellij --version 2>/dev/null | awk '{print $2}' | sed 's/^v//')"
  latest="$(fetch_latest_version zellij-org/zellij)"
  if [[ -n "$current" && "$current" == "$latest" ]]; then
    ok "zellij up to date (v$current)"
  else
    install_release_binary_tarball \
      "$latest" \
      "https://github.com/zellij-org/zellij/releases/download/v{VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
      "zellij" \
      "zellij"
    ok "zellij updated to v$latest"
  fi
else
  warn "zellij not installed; skipping"
fi

log "lazygit: update if newer release exists"
if command -v lazygit >/dev/null 2>&1; then
  current="$(lazygit --version 2>&1 | sed -n 's/.*version=\([^,]*\).*/\1/p' || true)"
  latest="$(fetch_latest_version jesseduffield/lazygit)"
  if [[ -n "$current" && "$current" == "$latest" ]]; then
    ok "lazygit up to date (v$current)"
  else
    install_release_binary_tarball \
      "$latest" \
      "https://github.com/jesseduffield/lazygit/releases/download/v{VERSION}/lazygit_{VERSION}_Linux_x86_64.tar.gz" \
      "lazygit" \
      "lazygit"
    ok "lazygit updated to v$latest"
  fi
else
  warn "lazygit not installed; skipping"
fi

log "pyenv: update (pyenv + plugins)"
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
if [[ -d "$PYENV_ROOT/.git" ]]; then
  git -C "$PYENV_ROOT" pull --ff-only || warn "pyenv pull failed"
  if [[ -d "$PYENV_ROOT/plugins" ]]; then
    for d in "$PYENV_ROOT"/plugins/*; do
      [[ -d "$d/.git" ]] || continue
      git -C "$d" pull --ff-only || warn "pyenv plugin update failed: $(basename "$d")"
    done
  fi
  ok "pyenv updated"
else
  warn "pyenv not installed; skipping"
fi

log "zsh-completions: update"
ZSH_COMP_DIR="$HOME/.zsh/zsh-completions"
if [[ -d "$ZSH_COMP_DIR/.git" ]]; then
  git -C "$ZSH_COMP_DIR" pull --ff-only
  ok "zsh-completions updated"
else
  warn "zsh-completions not installed; skipping"
fi

log "Claude: version check"
if command -v claude >/dev/null 2>&1; then
  ok "claude: $(claude --version 2>/dev/null || echo 'installed')"
else
  warn "claude not found"
fi

log "Version summary"
printf "  %-10s %s\n" "zellij:"  "$(zellij --version 2>/dev/null || echo n/a)"
printf "  %-10s %s\n" "lazygit:" "$(lazygit --version 2>/dev/null | sed -n 's/.*version=\([^,]*\).*/\1/p' || echo n/a)"
printf "  %-10s %s\n" "glow:"    "$(glow --version 2>/dev/null || echo n/a)"
printf "  %-10s %s\n" "gh:"      "$(gh --version 2>/dev/null | head -1 | awk '{print $3}' || echo n/a)"
printf "  %-10s %s\n" "node:"    "$(node --version 2>/dev/null || echo n/a)"
printf "  %-10s %s\n" "codex:"   "$(codex --version 2>/dev/null || echo n/a)"
printf "  %-10s %s\n" "claude:"  "$(claude --version 2>/dev/null || echo n/a)"

log "Maintenance complete"
