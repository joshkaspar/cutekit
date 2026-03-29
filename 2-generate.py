#!/usr/bin/env python3
"""
2-generate.py — cutekit config generator
Reads 1-config.yaml and generates all output files directly on the target machine.

Normal usage (run this on the machine you are setting up):
  python3 2-generate.py                    # reads 1-config.yaml
  python3 2-generate.py new-config.yaml   # reads a different file

Alternative: generate on a different machine, then transfer
  If Python cannot run on the target machine, you can run this script
  elsewhere and copy the generated files over before running 3-setup.sh.

  WARNING: This path is intended only for cases where the Python script
  cannot run directly on the target. Review 3-setup.sh carefully before
  running it, and note that some features (e.g. mise config written to
  ~/.config/mise/config.toml) will reflect the generating machine's home
  directory rather than the target's. Some steps may behave differently
  than running the script directly on the target machine.
"""

import sys
import os
import stat
from dataclasses import dataclass, field
from typing import Optional

import yaml  # pip install pyyaml


# ─── Data structure ───────────────────────────────────────────────────────────
#
# A dataclass is a clean way to define what a "Tool" looks like in Python.
# Think of it as a template: every tool loaded from the YAML will become
# one of these objects with named fields, instead of a raw dictionary
# where you have to remember what keys exist.
#
# Optional[str] means the field can hold a string or be absent (None).
# field(default_factory=list) means the default value is an empty list.
# We can't write `= []` directly in a dataclass — this is a Python quirk
# to prevent all instances from accidentally sharing the same list object.

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
#
# This function opens the YAML file and returns the raw contents as a
# plain Python dictionary. All YAML lists become Python lists,
# and all YAML key: value pairs become Python dict entries.

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
#
# This function takes the raw dictionary from the YAML and turns each entry
# in the `tools:` list into a proper Tool object (defined above).
#
# .get() is used instead of ["key"] everywhere. The difference:
#   data["key"]       → crashes with KeyError if the key doesn't exist
#   data.get("key")   → returns None if the key doesn't exist (safe)

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
#
# Walk every Tool object and check for configuration problems.
# We collect all warnings into a list instead of stopping at the first error,
# so the user can fix everything in one pass rather than one problem at a time.

# Every backend except 'apt' and 'curl' is managed by mise.
# This constant is referenced by both validate() and get_mise_tools() below.
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


# ─── M2: Build mise.toml ─────────────────────────────────────────────────────
#
# mise.toml only cares about tools that are installed through mise.
# In this project, that means every backend listed in MISE_BACKENDS above.
#
# We keep each concern in its own function so each function has one job:
#   get_mise_tools()         — filter the list
#   build_mise_plugin_name() — format one tool's identifier
#   build_mise_toml_text()   — assemble the full file content
#   write_mise_toml()        — orchestrate and write the file

def get_mise_tools(tools):
    """
    Returns only the tools that should be written into mise.toml.
    """
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
    Builds the full text content for ~/.config/mise/config.toml from a list of
    mise-managed tools.
    """
    lines = []

    # This header makes it obvious the file should not be edited by hand.
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

        # If the tool has no version specified in the config, default to "latest".
        # In Python, `x or y` returns y when x is None or empty.
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
    """
    Writes plain text to a file, replacing any previous contents.
    """
    with open(filepath, "w") as output_file:
        output_file.write(text)


def write_mise_toml(tools):
    """
    Filters mise-managed tools, converts them into TOML, and writes
    ~/.config/mise/config.toml (creating the directory if needed).
    """
    mise_tools = get_mise_tools(tools)
    mise_toml_text = build_mise_toml_text(mise_tools)

    mise_config_dir = os.path.expanduser("~/.config/mise")
    os.makedirs(mise_config_dir, exist_ok=True)
    config_path = os.path.join(mise_config_dir, "config.toml")
    write_text_file(config_path, mise_toml_text)

    print(f"Wrote: {config_path} ({len(mise_tools)} tools)")


# ─── M3: Build 3-setup.sh ────────────────────────────────────────────────────
#
# The setup script handles two things:
#   1. All apt installs — one combined `apt install` call for every apt package
#      and apt_dep across all tools
#   2. All curl installers — one block per tool, with optional skip-if logic
#
# Everything else (mise, cargo, npm, etc.) is NOT in this script.
# mise handles those after this script runs.
#
# Each concern has its own function, same pattern as the mise section above:
#   get_custom_setup_tools() — filter apt tools that need pre-apt commands
#   get_apt_packages()       — collect all apt package names
#   get_curl_tools()         — filter curl-backend tools
#   build_setup_sh_text()    — assemble the full script content
#   write_setup_sh()         — orchestrate and write the file

def get_custom_setup_tools(tools):
    """
    Returns only the apt tools that have a custom_setup block.
    These need special commands (like adding a GPG key or apt source) to run
    before apt can find and install the package.
    """
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
    """
    Returns only the tools that use the curl backend.
    """
    curl_tools = []

    for tool in tools:
        if tool.backend == "curl":
            curl_tools.append(tool)

    return curl_tools


# ─── Shell default ────────────────────────────────────────────────────────────
#
# If the config declares zsh as the target shell AND the zsh apt package is
# included in the tools list, the setup script can run `chsh` automatically.
# Both conditions must be true: chsh needs zsh installed to point at it,
# and zsh needs to have been declared so the apt block actually installs it.

def set_default_shell(config, tools):
    """
    Returns True if shell.family in the config is 'zsh'
    AND a tool with package: zsh is present in the tools list.

    This tells the script generator to include a `chsh` call.
    If either condition is missing, we skip it silently.
    """
    shell_family = config.get("shell", {}).get("family", "")

    if shell_family != "zsh":
        return False

    for tool in tools:
        if tool.package == "zsh":
            return True

    return False


def build_setup_sh_text(apt_packages, curl_tools, custom_setup_tools, set_default_shell):
    """
    Builds the full text content for 3-setup.sh.
    Accepts pre-filtered lists so this function only has to format, not filter.

    Script sections run in this order:
      0. Custom repository setup  — GPG keys and apt sources (before apt update)
      1. apt packages             — one combined install call
      2. mise tools               — `mise install` from the project directory
      3. curl installers          — one block per tool
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

    # Safety checks — bail out early if the environment isn't right.
    lines.append('if [[ $EUID -eq 0 ]]; then')
    lines.append('    echo "Run this script as a normal user, not as root."')
    lines.append("    exit 1")
    lines.append("fi")
    lines.append('if ! command -v sudo >/dev/null 2>&1; then')
    lines.append('    echo "sudo is required but was not found."')
    lines.append("    exit 1")
    lines.append("fi")
    lines.append("")

    # Bootstrap apt — curl and ca-certificates must be present before anything
    # else runs. Custom repository setup (Section 0) uses curl to fetch GPG keys,
    # so these two packages have to be installed first, separately from the main
    # apt block.
    lines.append('log "Bootstrapping prerequisites..."')
    lines.append("sudo apt-get update -qq")
    lines.append("sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl ca-certificates")
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
        lines.append("sudo apt-get update -qq")
        lines.append("sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \\")

        for package_name in apt_packages:
            lines.append(f"    {package_name} \\")

        # The final package line must not end with a backslash.
        lines[-1] = f"    {apt_packages[-1]}"

        lines.append('ok "apt packages installed"')
        lines.append("")

    # ── Section 2: mise tools ─────────────────────────────────────────────────
    # mise is now installed (from the apt section above).
    # `mise install` reads mise.toml in the current directory and installs every
    # tool declared there into ~/.local/share/mise — available globally.
    # If there are no mise tools, this is a fast no-op.
    lines.append('log "Installing mise-managed tools..."')
    lines.append("mise install")
    lines.append('ok "mise tools installed"')
    lines.append("")

    # ── Section 3: curl installers ────────────────────────────────────────────
    for tool in curl_tools:
        if tool.installed_check:
            # If the tool is already present, skip the curl install entirely and
            # tell the user to update it manually — curl installers are not safe
            # to re-run blindly.
            lines.append(f"if {tool.installed_check} > /dev/null 2>&1; then")
            lines.append(f'    log "{tool.name} already installed — to update, see the tool\'s own documentation"')
            lines.append("else")
            lines.append(f'    log "Installing {tool.name}..."')
            lines.append(f"    curl -fsSL {tool.url} | bash")
            lines.append(f'    ok "{tool.name} installed"')
            lines.append("fi")
        else:
            lines.append(f'log "Installing {tool.name}..."')
            lines.append(f"    curl -fsSL {tool.url} | bash")
            lines.append(f'ok "{tool.name} installed"')

        lines.append("")

    # ── Section 4: default shell ──────────────────────────────────────────────
    # Only runs if set_default_shell() confirmed both zsh and the zsh package
    # are declared in the config. Checks for chsh at runtime before calling it
    # so the script fails informatively rather than silently if chsh is absent.
    if set_default_shell:
        lines.append('log "Setting default shell to zsh..."')
        lines.append('if command -v chsh > /dev/null 2>&1; then')
        lines.append('    chsh -s "$(which zsh)"')
        lines.append('    ok "Default shell set to zsh — log out and back in to activate"')
        lines.append('else')
        lines.append('    echo "Warning: chsh not found — default shell was not changed"')
        lines.append('fi')
        lines.append('')

    # ── Done ──────────────────────────────────────────────────────────────────
    # Remind the user to check the post-install steps before moving on.
    lines.append('log "Setup complete!"')
    lines.append('echo ""')
    lines.append('echo "Next: open 4-post-install-steps.md for manual configuration steps."')
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
    change_shell = set_default_shell(config, tools)

    setup_sh_text = build_setup_sh_text(apt_packages, curl_tools, custom_setup_tools, change_shell)
    write_text_file("3-setup.sh", setup_sh_text)

    current_mode = os.stat("3-setup.sh").st_mode
    os.chmod("3-setup.sh", current_mode | stat.S_IEXEC)

    print(f"Wrote: 3-setup.sh ({len(custom_setup_tools)} custom repos, {len(apt_packages)} apt packages, {len(curl_tools)} curl installers)")


# ─── .zshrc.setup ─────────────────────────────────────────────────────────────
#
# Collects all shell_setup blocks from tools and writes them into a single
# sourced file. The output filename comes from the top-level shell: section
# in the config, so changing it there changes the output filename.

def build_zshrc_setup_text(tools_with_shell_setup):
    """
    Builds the full text content for .zshrc.setup from a list of tools
    that have shell_setup entries. Each tool gets a comment header and
    then its lines, separated by blank lines.
    """
    lines = []
    lines.append("# Generated by 2-generate.py — do not edit directly")

    for current_tool in tools_with_shell_setup:
        lines.append("")
        lines.append(f"# {current_tool.name}")
        for setup_line in current_tool.shell_setup:
            lines.append(setup_line)

    return "\n".join(lines)


def write_zshrc_setup(tools, config):
    """
    Filters tools that have shell_setup blocks, builds the file content,
    and writes it to the filename specified in the config's shell: section.
    """
    shell_section = config.get("shell", {})
    output_filename = shell_section.get("setup_file", ".zshrc.setup")

    tools_with_shell_setup = []
    for current_tool in tools:
        if current_tool.shell_setup:
            tools_with_shell_setup.append(current_tool)

    zshrc_setup_text = build_zshrc_setup_text(tools_with_shell_setup)
    write_text_file(output_filename, zshrc_setup_text)

    print(f"Wrote: {output_filename} ({len(tools_with_shell_setup)} tools)")


# ─── Markdown outputs ─────────────────────────────────────────────────────────
#
# Both post-install steps and the tool reference follow the same structure:
# a generated header, then one ## section per tool, with each list item
# written as a markdown bullet.

def build_post_install_text(tools_with_post_install):
    """
    Builds the full text content for 4-post-install-steps.md.
    Each tool gets a ## heading followed by its post_install items as bullets.
    """
    lines = []
    lines.append("<!-- Generated by 2-generate.py — do not edit directly -->")

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
      1. A grouped summary table of every tool (from build_summary_table_lines)
      2. A ## section per tool that has reference notes
    """
    lines = []
    lines.append("<!-- Generated by 2-generate.py — do not edit directly -->")
    lines.append("")

    # Section 1: the full tool table, so it lives in the file rather than
    # disappearing after the terminal session ends.
    lines.append("## Tool summary")
    lines.append("")
    lines.append("```")
    for table_line in build_summary_table_lines(all_tools):
        lines.append(table_line)
    lines.append("```")

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

    Passes all tools (for the summary table) and only the tools with reference
    notes (for the per-tool sections) into the builder.
    """
    tools_with_reference = []
    for current_tool in tools:
        if current_tool.reference:
            tools_with_reference.append(current_tool)

    tool_reference_text = build_tool_reference_text(tools, tools_with_reference)
    write_text_file("tool-reference.md", tool_reference_text)

    print(f"Wrote: tool-reference.md ({len(tools_with_reference)} tools with notes)")


# ─── Summarize ────────────────────────────────────────────────────────────────
#
# Two functions here:
#   build_summary_table_lines() — builds the grouped table as a list of text
#                                  lines, so it can be reused in tool-reference.md
#   print_summary()             — prints a short user-facing list to stdout
#                                  showing what will be installed and by what method

def build_summary_table_lines(tools):
    """
    Builds a grouped table of all tools as a list of plain-text lines.
    Returns the list so it can be written into tool-reference.md or printed.

    The :<20 and :<30 inside the f-string are column width specifiers —
    they pad the value with spaces so all columns line up neatly.
    """
    lines = []
    lines.append(f"cutekit — {len(tools)} tools")
    lines.append("=" * 55)

    # Build a dict that groups tools by backend: { "apt": [tool, tool], ... }
    groups = {}
    for tool in tools:
        if tool.backend not in groups:
            groups[tool.backend] = []
        groups[tool.backend].append(tool)

    # One section per backend, sorted alphabetically
    for backend in sorted(groups):
        lines.append(f"\n  [{backend}]")

        for tool in groups[backend]:

            # Show the install identifier appropriate for this backend
            if backend == "apt":
                detail = tool.package or ""
            else:
                detail = tool.source or ""

            # Collect which optional metadata fields are present as short flags
            flags = []
            if tool.shell_setup:  flags.append("shell")
            if tool.post_install: flags.append("post_install")
            if tool.reference:    flags.append("ref")
            if tool.apt_deps:     flags.append("apt_deps")
            if tool.expose:       flags.append("expose")

            # Only show the bracket if there are flags to show
            if flags:
                flag_str = "  [" + ", ".join(flags) + "]"
            else:
                flag_str = ""

            lines.append(f"    {tool.name:<20} {detail:<30}{flag_str}")

    return lines


def print_summary(tools):
    """
    Prints a short user-facing list to stdout showing what will be installed
    and by what method. The full detail table goes into tool-reference.md.
    """
    # Collect apt tool names and mise tool names into separate lists
    apt_tool_names = []
    mise_tool_names = []

    for tool in tools:
        if tool.backend == "apt":
            apt_tool_names.append(tool.name)
        elif tool.backend in MISE_BACKENDS:
            mise_tool_names.append(tool.name)

    print()
    print("System packages to be installed via apt:")
    for tool_name in apt_tool_names:
        print(f"  - {tool_name}")

    print()
    print("TUI tools to be installed via mise:")
    for tool_name in mise_tool_names:
        print(f"  - {tool_name}")

    print()


# ─── Main ─────────────────────────────────────────────────────────────────────
#
# This is the entry point. It runs all steps in order and passes data
# from each step to the next.

def main():
    # Accept an optional filename argument; fall back to the default config
    if len(sys.argv) > 1:
        config_file = sys.argv[1]
    else:
        config_file = "1-config.yaml"

    print(f"Reading: {config_file}")

    # Step 1 — Load raw YAML into a Python dict
    config = load_yaml(config_file)

    # Step 2 — Get the tools list out of the top-level dict
    raw_tools = config.get("tools")
    if not raw_tools:
        print("Error: No 'tools:' list found in config.")
        sys.exit(1)

    # Step 3 — Convert raw dicts into Tool objects
    tools = parse_tools(raw_tools)

    # Step 4 — Check for problems and report them
    warnings = validate(tools)
    if warnings:
        print(f"\n  Warnings ({len(warnings)}):")
        for warning in warnings:
            print(warning)

    # Step 5 — Print the summary table
    print_summary(tools)

    # Step 6 — Write mise.toml for all mise-managed tools
    write_mise_toml(tools)

    # Step 7 — Write 3-setup.sh for apt and curl tools
    write_setup_sh(tools, config)

    # Step 8 — Write .zshrc.setup for tools with shell_setup blocks
    write_zshrc_setup(tools, config)

    # Step 9 — Write 4-post-install-steps.md
    write_post_install(tools)

    # Step 10 — Write tool-reference.md
    write_tool_reference(tools)


if __name__ == "__main__":
    main()
