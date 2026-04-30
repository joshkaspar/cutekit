#!/usr/bin/env python3
"""
2-generate.py — CUTEkit config generator
Reads 1-config.yaml and generates all output files into the current directory.

All generated files land here, next to the config:
  3-setup.sh            — runs as root; creates user, installs packages
  mise.toml             — mise tool manifest (3-setup.sh moves this into place)
  .zshrc.setup          — shell init snippets
  4-post-install-steps.md
  tool-reference.md
  99-lock-doors.sh      — SSH hardening (run only after confirming new user login)

Usage:
  python3 2-generate.py                    # reads 1-config.yaml
  python3 2-generate.py new-config.yaml   # reads a different file
"""

import sys
import os
import stat
from dataclasses import dataclass, field
from typing import Optional

import yaml  # pip install pyyaml


# ─── Data structure ───────────────────────────────────────────────────────────
#
# Dataclass instead of raw dict: named fields catch typos at load time

@dataclass
class Tool:
    name:         str                        # Human-friendly label
    backend:      str                        # How it gets installed
    package:      Optional[str] = None       # apt package name (apt backend only)
    source:       Optional[str] = None       # mise source identifier
    version:      Optional[str] = None       # mise version (usually "latest")
    shell_setup:  list = field(default_factory=list)  # Lines to write into .zshrc.setup
    post_install: list          = field(default_factory=list)  # Actionable steps
    reference:    list          = field(default_factory=list)  # Background notes
    apt_deps:     list          = field(default_factory=list)  # Extra apt prereqs
    installed_check: Optional[str] = None    # Bash expression to detect an existing install; skips setup if true
    expose:       Optional[dict] = None      # Binary rename: {installed_binary, as}
    url:          Optional[str] = None       # curl backend: the URL to pipe to bash
    custom_setup: Optional[str] = None       # apt backend: bash commands to run before apt (e.g. adding a GPG key)


# ─── Load ─────────────────────────────────────────────────────────────────────

def load_yaml(filepath):
    """
    Opens a YAML file and converts it into a Python dictionary.
    Returns the dictionary, or exits with an error message if the file
    is missing or contains invalid YAML.
    """
    try:
        with open(filepath, "r") as input_file:
            return yaml.safe_load(input_file)

    except FileNotFoundError:
        print(f"Error: The file '{filepath}' was not found.")
        sys.exit(1)

    except yaml.YAMLError as yaml_error:
        print(f"Error: Could not parse '{filepath}' as YAML.")
        print(f"Detail: {yaml_error}")
        sys.exit(1)


# ─── Parse ────────────────────────────────────────────────────────────────────

def parse_tools(list_from_yaml):
    """
    Converts raw YAML dictionary data into a list of Tool objects.
    Each entry in the YAML tools list becomes one Tool with named fields.
    """
    tools = []

    for tool_dictionary in list_from_yaml:
        processed_tool = Tool(
            name         = tool_dictionary.get("name",         "<unnamed>"),
            backend      = tool_dictionary.get("backend",      "mise"),
            package      = tool_dictionary.get("package"),
            source       = tool_dictionary.get("source"),
            version      = tool_dictionary.get("version"),
            shell_setup  = tool_dictionary.get("shell_setup", []),
            post_install = tool_dictionary.get("post_install", []),
            reference    = tool_dictionary.get("reference",    []),
            apt_deps     = tool_dictionary.get("apt_deps",     []),
            installed_check = tool_dictionary.get("installed_check"),
            expose       = tool_dictionary.get("expose"),
            url          = tool_dictionary.get("url"),
            custom_setup = tool_dictionary.get("custom_setup"),
        )

        tools.append(processed_tool)

    return tools


# ─── Validate ─────────────────────────────────────────────────────────────────

# Every backend except 'apt' and 'curl' is managed by mise.
# Referenced by both validate() and get_mise_tools() below.
MISE_BACKENDS = {"mise", "aqua", "github", "gitlab", "npm", "pipx", "cargo", "go", "asdf"}

def validate(tools):
    """
    Checks every Tool object for configuration problems.
    Returns a list of warning strings — one per problem found.
    An empty list means the config is clean.
    """
    warnings = []

    for tool in tools:
        if tool.backend == "apt" and not tool.package:
            warnings.append(f"  [{tool.name}] backend 'apt' requires a 'package' field")

        elif tool.backend in MISE_BACKENDS and tool.backend != "mise" and not tool.source:
            warnings.append(f"  [{tool.name}] backend '{tool.backend}' should have a 'source' field")

        elif tool.backend == "curl" and not tool.url:
            warnings.append(f"  [{tool.name}] backend 'curl' requires a 'url' field")

    return warnings


# ─── Build mise.toml ─────────────────────────────────────────────────────
#
# mise.toml only cares about tools that are installed through mise.
#
#   get_mise_tools()         — filter the list
#   build_mise_plugin_name() — format one tool's identifier
#   build_mise_toml_text()   — assemble the full file content
#   write_mise_toml()        — orchestrate and write the file

def get_mise_tools(tools):
    mise_tools = []
    
    for tool in tools:
        if tool.backend in MISE_BACKENDS:
            mise_tools.append(tool)

    return mise_tools


def build_mise_plugin_name(tool):
    """
    Converts one Tool object into the key mise expects in config.toml.

    Examples:
      backend=mise                             -> fzf  (registry lookup by name)
      backend=aqua,   source=cli/cli          -> aqua:cli/cli
      backend=npm,    source=@openai/codex    -> npm:@openai/codex
    """
    if tool.backend == "mise":
        return tool.name
    return f"{tool.backend}:{tool.source}"


def build_mise_toml_text(mise_tools):
    """
    Builds the full text content for mise.toml from a list of mise-managed tools.
    3-setup.sh moves this file into the target user's ~/.config/mise/ directory.
    """
    lines = []

    lines.append("# Generated by 2-generate.py")
    lines.append("")

    # [settings] must appear before [tools] so TOML parses it as a top-level
    # section rather than a subtable of [tools].
    lines.append("[settings]")
    lines.append("lockfile = true")
    lines.append("")

    lines.append("[tools]")

    # Sort by plugin name so the output is stable and easy to diff across runs.
    sorted_tools = sorted(mise_tools, key=build_mise_plugin_name)

    for tool in sorted_tools:
        plugin_name = build_mise_plugin_name(tool)

        tool_version = tool.version or "latest"

        if ":" in plugin_name:
            # Explicit backend required — tool is not available by short name in
            # the mise registry and must be addressed via its full backend:source path.
            lines.append(f'"{plugin_name}" = "{tool_version}"')
        else:
            lines.append(f'{plugin_name} = "{tool_version}"')

    lines.append("")

    return "\n".join(lines)


def write_text_file(filepath, text):
    with open(filepath, "w") as output_file:
        output_file.write(text)


def write_mise_toml(tools):
    """
    Filters mise-managed tools, converts them into TOML, and writes mise.toml
    into the current (project) directory. 3-setup.sh will move this file into
    the target user's ~/.config/mise/ directory during setup.
    """
    mise_tools = get_mise_tools(tools)
    mise_toml_text = build_mise_toml_text(mise_tools)

    write_text_file("mise.toml", mise_toml_text)

    print(f"Wrote: mise.toml ({len(mise_tools)} tools)")


# ─── Build 3-setup.sh ────────────────────────────────────────────────────
#
# The setup script handles two things:
#   1. All apt installs — one combined `apt install` call for every apt package
#      and apt_dep across all tools
#   2. All curl installers — one block per tool, with optional skip-if logic
#
# Everything else (mise, cargo, npm, etc.) is NOT in this script.
# mise handles those after this script runs.

def get_custom_setup_tools(tools):
    custom_setup_tools = []

    for tool in tools:
        if tool.backend == "apt" and tool.custom_setup:
            custom_setup_tools.append(tool)

    return custom_setup_tools


def get_apt_packages(tools):
    """
    Collects every apt package name needed across all tools.

    This includes two sources:
      - tools with backend: apt  (their `package` field)
      - any tool with `apt_deps` (prerequisite packages needed before install)

    Returns a sorted list so the output is stable across runs.
    Using a set internally ensures no duplicates if two tools share a dep.
    """
    apt_packages = set()

    for tool in tools:
        if tool.backend == "apt" and tool.package:
            apt_packages.add(tool.package)

        for dep in tool.apt_deps:
            apt_packages.add(dep)

    return sorted(apt_packages)


def get_curl_tools(tools):
    curl_tools = []

    for tool in tools:
        if tool.backend == "curl":
            curl_tools.append(tool)

    return curl_tools


def build_setup_sh_text(apt_packages, curl_tools, custom_setup_tools, system_config):
    """
    Builds the full text content for 3-setup.sh.
    Accepts pre-filtered lists so this function only has to format, not filter.
    system_config is the parsed system: section from 1-config.yaml.

    Script sections run in this order:
      0.  Custom repository setup  — GPG keys and apt sources (before apt update)
      1.  apt packages             — one combined install call
      1.5 User setup               — create user, set shell, copy SSH keys, move config files
      2.  mise tools               — `mise install` run as the target user
      3.  curl installers          — one block per tool, run as the target user
      4.  Two-terminal warning     — safety instructions before SSH hardening
    """
    lines = []

    lines.append("#!/usr/bin/env bash")
    lines.append("# Generated by 2-generate.py")
    lines.append("# Do not edit directly — edit 1-config.yaml and regenerate.")
    lines.append("")
    lines.append("set -euo pipefail")
    lines.append("")

    # log() prints a bold blue section header.
    # ok() prints a green confirmation line.
    # The \033[ codes are ANSI escape sequences for color; \033[0m resets it.
    lines.append('log() { printf "\\n\\033[1;34m==> %s\\033[0m\\n" "$1"; }')
    lines.append('ok()  { printf "    \\033[0;32mOK: %s\\033[0m\\n" "$1"; }')
    lines.append("")

    # This script must run as root — it creates a user and modifies system files.
    lines.append('if [[ $EUID -ne 0 ]]; then')
    lines.append('    echo "This script must be run as root: sudo bash 3-setup.sh"')
    lines.append("    exit 1")
    lines.append("fi")
    lines.append("")

    # SCRIPT_DIR points to the directory containing this script.
    # Using BASH_SOURCE[0] instead of $0 ensures it works when the script is
    # sourced or called via a path like /path/to/3-setup.sh.
    # We need this to find mise.toml and .zshrc.setup regardless of what
    # directory the caller is in when they run the script.
    lines.append('SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"')
    lines.append("")

    # Read target_user and target_password from config, or prompt if blank.
    # Prompting at runtime keeps credentials out of the generated file.
    target_user = system_config.get("target_user") or ""
    target_password = system_config.get("target_password") or ""
    copy_root_ssh_keys = system_config.get("copy_root_ssh_keys", True)

    lines.append(f'TARGET_USER="{target_user}"')
    lines.append('if [[ -z "$TARGET_USER" ]]; then')
    lines.append('    read -rp "Enter the username to create: " TARGET_USER')
    lines.append("fi")
    lines.append("")

    lines.append(f'TARGET_PASSWORD="{target_password}"')
    lines.append('if [[ -z "$TARGET_PASSWORD" ]]; then')
    lines.append('    read -rsp "Enter a password for $TARGET_USER: " TARGET_PASSWORD')
    lines.append('    echo')
    lines.append("fi")
    lines.append("")

    # Bootstrap apt — curl and ca-certificates must be present before anything
    # else runs. Custom repository setup (Section 0) uses curl to fetch GPG keys,
    # so these two packages have to be installed first, separately from the main
    # apt block.
    lines.append('log "Bootstrapping prerequisites..."')
    lines.append("apt-get update -qq")
    lines.append("DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates")
    lines.append('ok "Prerequisites ready"')
    lines.append("")

    # ── Section 0: Custom repository setup ───────────────────────────────────
    # Some apt tools (like mise) need a GPG key and a custom apt source added
    # before `apt install` can find them. Those commands live here, between
    # the bootstrap apt-get update and the main one — so the newly added sources
    # are picked up when the main apt block runs its own apt-get update.
    #
    # Each block is wrapped in the tool's installed_check guard so that
    # re-running the script on an already-configured machine doesn't add
    # duplicate keys or sources.
    if custom_setup_tools:
        lines.append('log "Configuring custom repositories..."')
        lines.append("")

        for tool in custom_setup_tools:
            lines.append(f"# {tool.name}")

            if tool.installed_check:
                # Only run setup if the tool is not already installed.
                lines.append(f"if ! {tool.installed_check} > /dev/null 2>&1; then")

                # Each line of custom_setup is indented inside the if block.
                # .strip() removes any leading/trailing blank lines from the YAML block.
                # .splitlines() breaks the multiline string into individual lines.
                for setup_line in tool.custom_setup.strip().splitlines():
                    lines.append(f"    {setup_line}")

                lines.append("fi")
            else:
                # No guard — always run the setup commands.
                for setup_line in tool.custom_setup.strip().splitlines():
                    lines.append(setup_line)

            lines.append("")

        lines.append('ok "Custom repositories configured"')
        lines.append("")

    # ── Section 1: apt ────────────────────────────────────────────────────────
    # One combined install call covers both apt-backend tools and apt_deps.
    # DEBIAN_FRONTEND=noninteractive prevents apt from pausing to ask questions.
    # Packages are listed one per line (with backslash continuation) so the
    # generated script is easy to read and diff.
    if apt_packages:
        lines.append('log "Installing apt packages..."')
        lines.append("apt-get update -qq")
        lines.append("DEBIAN_FRONTEND=noninteractive apt-get install -y \\")

        for package_name in apt_packages:
            lines.append(f"    {package_name} \\")

        # The final package line must not end with a backslash.
        lines[-1] = f"    {apt_packages[-1]}"

        lines.append('ok "apt packages installed"')
        lines.append("")

    # ── Section 1.5: User setup ───────────────────────────────────────────────
    # All apt packages (including zsh) are now installed.
    # We can safely create the user, set their shell, copy SSH keys,
    # and place the generated config files in their home directory.

    lines.append('log "Creating user $TARGET_USER..."')
    lines.append('if id "$TARGET_USER" &>/dev/null; then')
    lines.append('    ok "User $TARGET_USER already exists — skipping creation"')
    lines.append("else")
    lines.append('    useradd -m -s /usr/bin/zsh "$TARGET_USER"')
    lines.append('    echo "$TARGET_USER:$TARGET_PASSWORD" | chpasswd')
    lines.append('    usermod -aG sudo "$TARGET_USER"')
    lines.append('    ok "User $TARGET_USER created and added to sudo group"')
    lines.append("fi")
    lines.append("")
    lines.append('if [ "$(getent passwd "$TARGET_USER" | cut -d: -f7)" != "/usr/bin/zsh" ]; then')
    lines.append('    log "Setting zsh as default shell for $TARGET_USER..."')
    lines.append('    chsh -s /usr/bin/zsh "$TARGET_USER"')
    lines.append('    ok "Shell updated"')
    lines.append("fi")
    lines.append("")

    # Copy root's authorized_keys to the new user so they can log in via SSH key.
    # This is the safety net — the user should confirm key-based login works
    # before running 99-lock-doors.sh to disable root access.
    if copy_root_ssh_keys:
        lines.append('log "Copying SSH keys to $TARGET_USER..."')
        lines.append('USER_HOME="/home/$TARGET_USER"')
        lines.append('mkdir -p "$USER_HOME/.ssh"')
        lines.append('if [[ -f /root/.ssh/authorized_keys ]]; then')
        lines.append('    cp /root/.ssh/authorized_keys "$USER_HOME/.ssh/authorized_keys"')
        lines.append('    chmod 700 "$USER_HOME/.ssh"')
        lines.append('    chmod 600 "$USER_HOME/.ssh/authorized_keys"')
        lines.append('    chown -R "$TARGET_USER:$TARGET_USER" "$USER_HOME/.ssh"')
        lines.append('    ok "SSH keys copied"')
        lines.append("else")
        lines.append('    echo "    WARNING: /root/.ssh/authorized_keys not found — skipping key copy"')
        lines.append("fi")
        lines.append("")

    # Move the generated config files from the project directory into the
    # target user's home directory, then fix ownership so the user owns them.
    lines.append('log "Installing configuration files..."')
    lines.append('USER_HOME="/home/$TARGET_USER"')
    lines.append('mkdir -p "$USER_HOME/.config/mise"')
    lines.append('cp "$SCRIPT_DIR/mise.toml" "$USER_HOME/.config/mise/config.toml"')
    lines.append('cp "$SCRIPT_DIR/.zshrc.setup" "$USER_HOME/.zshrc"')
    lines.append('chown -R "$TARGET_USER:$TARGET_USER" "$USER_HOME/.config"')
    lines.append('chown "$TARGET_USER:$TARGET_USER" "$USER_HOME/.zshrc"')
    lines.append('mkdir -p "$USER_HOME/cutekit"')
    lines.append('cp "$SCRIPT_DIR/99-lock-doors.sh" "$USER_HOME/cutekit/99-lock-doors.sh"')
    lines.append('cp "$SCRIPT_DIR/4-post-install-steps.md" "$USER_HOME/cutekit/4-post-install-steps.md"')
    lines.append('chown -R "$TARGET_USER:$TARGET_USER" "$USER_HOME/cutekit"')
    lines.append('ok "Configuration files installed"')
    lines.append("")

    # ── Section 2: mise tools ─────────────────────────────────────────────────
    # mise is now installed (from the apt section above).
    # We run `mise install` as the target user so all tools land in their home
    # directory, not in root's. `su - $TARGET_USER -c "..."` starts a login
    # shell as that user, so mise's config in ~/.config/mise/config.toml is found.
    lines.append('log "Installing mise-managed tools..."')
    lines.append('su - "$TARGET_USER" -c "mise install"')
    lines.append('ok "mise tools installed"')
    lines.append("")

    # ── Section 3: curl installers ────────────────────────────────────────────
    # Same pattern: run each curl installer as the target user so the tool
    # installs into their home directory, not root's.
    for tool in curl_tools:
        if tool.installed_check:
            # If the tool is already present for this user, skip the curl install
            # and tell the user to update it manually — curl installers are not
            # safe to re-run blindly.
            lines.append(f'if su - "$TARGET_USER" -c "{tool.installed_check}" > /dev/null 2>&1; then')
            lines.append(f'    log "{tool.name} already installed — to update, see the tool\'s own documentation"')
            lines.append("else")
            lines.append(f'    log "Installing {tool.name}..."')
            lines.append(f'    su - "$TARGET_USER" -c "curl -fsSL {tool.url} | bash"')
            lines.append(f'    ok "{tool.name} installed"')
            lines.append("fi")
        else:
            lines.append(f'log "Installing {tool.name}..."')
            lines.append(f'su - "$TARGET_USER" -c "curl -fsSL {tool.url} | bash"')
            lines.append(f'ok "{tool.name} installed"')

        lines.append("")

    # ── Section 4: Two-terminal warning ──────────────────────────────────────
    # The setup script deliberately does NOT touch SSH config.
    # The user must verify they can log in as the new user before running
    # 99-lock-doors.sh to disable root access and password logins.
    # THIS_IP makes the SSH test command copy-paste ready.
    lines.append('THIS_IP=$(hostname -I | awk \'{ print $1 }\')')
    lines.append('log "Setup complete!"')
    lines.append('echo ""')
    lines.append('printf "\\033[1;33m"')
    lines.append('echo "  NEXT STEPS:"')
    lines.append('echo "  ─────────────────────────────────────────────────────────"')
    lines.append('echo "  1. Do NOT close this window."')
    lines.append('echo "  2. Open a NEW terminal on your local machine."')
    lines.append('echo "  3. Verify you can log in via SSH key:"')
    lines.append('echo "       ssh $TARGET_USER@$THIS_IP"')
    lines.append('echo ""')
    lines.append('echo "  4. Once logged in as $TARGET_USER, run the hardening script:"')
    lines.append('echo "       sudo bash /home/$TARGET_USER/cutekit/99-lock-doors.sh"')
    lines.append('echo ""')
    lines.append('echo "  5. Review post-install steps:"')
    lines.append('echo "       glow /home/$TARGET_USER/cutekit/4-post-install-steps.md"')
    lines.append('printf "\\033[0m"')
    lines.append('echo ""')
    lines.append("")

    return "\n".join(lines)


def write_setup_sh(tools, config):
    """
    Filters all three tool categories, builds the script, and writes 3-setup.sh.
    Filtering happens here so the counts are available for both the file content
    and the confirmation message, without running the filters twice.
    """
    custom_setup_tools = get_custom_setup_tools(tools)
    apt_packages = get_apt_packages(tools)
    curl_tools = get_curl_tools(tools)

    # Pass the system: section from config so the script can embed target_user
    # and other system settings, or prompt for them at runtime if left blank.
    system_config = config.get("system", {})

    setup_sh_text = build_setup_sh_text(apt_packages, curl_tools, custom_setup_tools, system_config)
    write_text_file("3-setup.sh", setup_sh_text)

    current_mode = os.stat("3-setup.sh").st_mode
    os.chmod("3-setup.sh", current_mode | stat.S_IEXEC)

    print(f"Wrote: 3-setup.sh ({len(custom_setup_tools)} custom repos, {len(apt_packages)} apt packages, {len(curl_tools)} curl installers)")


# ─── .zshrc.setup ─────────────────────────────────────────────────────────────

def build_zshrc_setup_text(tools_with_shell_setup, shell_defaults):
    """
    Builds the full text content for .zshrc.setup.

    The file has two sections:
      1. ZSH ENVIRONMENT — the defaults block from the config's shell.defaults field.
         Active by default; the user can remove or override any lines.
      2. REQUIRED TOOL-SPECIFIC ENTRIES — shell_setup lines from each tool.
         These must be present for the installed tools to function.
    """
    lines = []
    lines.append("# Generated by 2-generate.py — do not edit directly")
    lines.append("")

    # Section 1: zsh environment defaults.
    if shell_defaults:
        for line in shell_defaults.strip().splitlines():
            lines.append(line)
        lines.append("")

    # Section 2: tool-specific shell init.
    lines.append("# === REQUIRED TOOL-SPECIFIC ENTRIES ===")
    lines.append("# These must be in your .zshrc before the installed tools will work")

    for current_tool in tools_with_shell_setup:
        lines.append("")
        lines.append(f"# {current_tool.name}")
        for setup_line in current_tool.shell_setup:
            lines.append(setup_line)

    lines.append("")

    return "\n".join(lines)


def write_zshrc_setup(tools, config):
    """
    Filters tools that have shell_setup blocks, builds the file content,
    and writes it to the filename specified in the config's shell: section.
    """
    shell_section = config.get("shell", {})
    output_filename = shell_section.get("setup_file", ".zshrc.setup")
    shell_defaults = shell_section.get("defaults", "")

    tools_with_shell_setup = []
    for current_tool in tools:
        if current_tool.shell_setup:
            tools_with_shell_setup.append(current_tool)

    zshrc_setup_text = build_zshrc_setup_text(tools_with_shell_setup, shell_defaults)
    write_text_file(output_filename, zshrc_setup_text)

    print(f"Wrote: {output_filename} ({len(tools_with_shell_setup)} tools)")


# ─── Markdown outputs ─────────────────────────────────────────────────────────


def build_post_install_text(tools_with_post_install):
    """
    Builds the full text content for 4-post-install-steps.md.
    Each tool gets a ## heading followed by its post_install items as bullets.
    """
    lines = []
    lines.append("<!-- Generated by 2-generate.py -->")

    for current_tool in tools_with_post_install:
        lines.append("")
        lines.append(f"## {current_tool.name}")
        for step in current_tool.post_install:
            lines.append(f"- {step}")

    return "\n".join(lines)


def write_post_install(tools):
    """
    Filters tools that have post_install entries, builds the markdown content,
    and writes it to 4-post-install-steps.md.
    """
    tools_with_post_install = []
    for current_tool in tools:
        if current_tool.post_install:
            tools_with_post_install.append(current_tool)

    post_install_text = build_post_install_text(tools_with_post_install)
    write_text_file("4-post-install-steps.md", post_install_text)

    print(f"Wrote: 4-post-install-steps.md ({len(tools_with_post_install)} tools)")


def build_tool_reference_text(all_tools, tools_with_reference):
    """
    Builds the full text content for tool-reference.md.

    The file has two sections:
      1. The apt/mise install list (same list printed to stdout during generation)
      2. A ## section per tool that has reference notes
    """
    lines = []
    lines.append("<!-- Generated by 2-generate.py — do not edit directly -->")
    lines.append("")

    # Section 1: what gets installed, so the list is preserved after the terminal
    # session ends. build_install_list_lines() is the same helper used by
    # print_summary(), so this file always matches what the generator printed.
    lines.append("## What gets installed")
    lines.append("")
    for list_line in build_install_list_lines(all_tools):
        lines.append(list_line)

    # Section 2: per-tool reference notes
    for current_tool in tools_with_reference:
        lines.append("")
        lines.append(f"## {current_tool.name}")
        for note in current_tool.reference:
            lines.append(f"- {note}")

    return "\n".join(lines)


def write_tool_reference(tools):
    """
    Filters tools that have reference entries, builds the markdown content,
    and writes it to tool-reference.md.

    Passes all tools (for the install list) and only the tools with reference
    notes (for the per-tool sections) into the builder.
    """
    tools_with_reference = []
    for current_tool in tools:
        if current_tool.reference:
            tools_with_reference.append(current_tool)

    tool_reference_text = build_tool_reference_text(tools, tools_with_reference)
    write_text_file("tool-reference.md", tool_reference_text)

    print(f"Wrote: tool-reference.md ({len(tools_with_reference)} tools with notes)")


# ─── 99-lock-doors.sh ────────────────────────────────────────────────────────
#
# This is the "kill-switch" script — it hardens SSH by disabling root login
# and password authentication. It must ONLY be run after the user has confirmed
# they can log in as the target user via SSH key. Running it prematurely locks
# everyone out.
#
# Rather than modifying /etc/ssh/sshd_config directly (fragile, easy to break),
# we write a drop-in file to /etc/ssh/sshd_config.d/. Drop-in files override
# the main config and are the recommended approach on modern Ubuntu.
#
# Generation is controlled by the ssh_hardening flag in the system: config block.

def build_lock_doors_sh_text(system_config):
    lines = []

    lines.append("#!/usr/bin/env bash")
    lines.append("# Generated by 2-generate.py")
    lines.append("# Do not edit directly — edit 1-config.yaml and regenerate.")
    lines.append("#")
    lines.append("# WARNING: Only run this after confirming you can SSH in as the new user.")
    lines.append("# Running this script locks out root and disables password logins.")
    lines.append("")
    lines.append("set -euo pipefail")
    lines.append("")

    lines.append('log() { printf "\\n\\033[1;34m==> %s\\033[0m\\n" "$1"; }')
    lines.append('ok()  { printf "    \\033[0;32mOK: %s\\033[0m\\n" "$1"; }')
    lines.append("")

    # This script modifies system SSH config, so it must run as root.
    lines.append('if [[ $EUID -ne 0 ]]; then')
    lines.append('    echo "This script must be run as root: sudo bash 99-lock-doors.sh"')
    lines.append("    exit 1")
    lines.append("fi")
    lines.append("")

    # Write the drop-in hardening config.
    # /etc/ssh/sshd_config.d/ files are included by the main sshd_config on
    # Ubuntu 22.04+ and override any matching settings in the main file.
    lines.append('log "Writing SSH hardening config..."')
    lines.append('HARDENING_FILE="/etc/ssh/sshd_config.d/99-hardening.conf"')
    lines.append('cat > "$HARDENING_FILE" << \'EOF\'')
    lines.append("# CUTEkit SSH hardening — generated by 2-generate.py")
    lines.append("# Disables root login and password authentication.")
    lines.append("PermitRootLogin no")
    lines.append("PasswordAuthentication no")
    lines.append("EOF")
    lines.append('ok "Hardening config written to $HARDENING_FILE"')
    lines.append("")

    # Test the config before reloading — sshd -t catches syntax errors and
    # will exit non-zero if something is wrong, stopping the script before
    # any changes take effect.
    lines.append('log "Testing SSH config..."')
    lines.append("sshd -t")
    lines.append('ok "SSH config is valid"')
    lines.append("")

    # Reload sshd to pick up the new drop-in file.
    # `reload` applies config changes without dropping existing connections,
    # so the current root session stays open if something goes wrong.
    lines.append('log "Reloading SSH daemon..."')
    lines.append("systemctl reload ssh")
    lines.append('ok "SSH daemon reloaded"')
    lines.append("")

    lines.append('log "Doors locked."')
    lines.append('echo ""')
    lines.append('echo "  Root login and password authentication are now disabled."')
    lines.append('echo "  Key-based login as your user is the only way in."')
    lines.append('echo ""')

    return "\n".join(lines)


def write_lock_doors_sh(config):
    system_config = config.get("system", {})
    ssh_hardening = system_config.get("ssh_hardening", True)

    if not ssh_hardening:
        return

    lock_doors_text = build_lock_doors_sh_text(system_config)
    write_text_file("99-lock-doors.sh", lock_doors_text)

    current_mode = os.stat("99-lock-doors.sh").st_mode
    os.chmod("99-lock-doors.sh", current_mode | stat.S_IEXEC)

    print("Wrote: 99-lock-doors.sh")


# ─── Summarize ────────────────────────────────────────────────────────────────
#
# build_install_list_lines() builds the apt/mise install list as plain-text lines.
# It is shared by print_summary() (stdout) and build_tool_reference_text() (file)

def build_install_list_lines(tools):
    apt_tool_names = []
    mise_tool_names = []

    for tool in tools:
        if tool.backend == "apt":
            apt_tool_names.append(tool.name)
        elif tool.backend in MISE_BACKENDS:
            mise_tool_names.append(tool.name)

    lines = []
    lines.append("System packages to be installed via apt:")
    for tool_name in apt_tool_names:
        lines.append(f"  - {tool_name}")

    lines.append("")
    lines.append("TUI tools to be installed via mise:")
    for tool_name in mise_tool_names:
        lines.append(f"  - {tool_name}")

    return lines



def print_summary(tools):
    print()
    for line in build_install_list_lines(tools):
        print(line)
    print()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Accept an optional filename argument; fall back to the default config
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
    else:
        config_file = "1-config.yaml"

    print(f"Reading: {config_file}")

    config = load_yaml(config_file)

    raw_tools = config.get("tools")
    if not raw_tools:
        print("Error: No 'tools:' list found in config.")
        sys.exit(1)

    tools = parse_tools(raw_tools)
    warnings = validate(tools)
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for warning in warnings:
            print(warning)

    print_summary(tools)
    write_mise_toml(tools)
    write_setup_sh(tools, config)
    write_zshrc_setup(tools, config)
    write_post_install(tools)
    write_tool_reference(tools)
    write_lock_doors_sh(config)  # SSH hardening kill-switch

    print()
    print("Files generated. Next: bash 3-setup.sh")


if __name__ == "__main__":
    main()
