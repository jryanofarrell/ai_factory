#!/usr/bin/env bash
# Bootstrap ai_factory on a fresh Linux host (target: always-on gaming PC).
#
# Idempotent: skips anything already installed. Safe to re-run.
# After this finishes successfully, edit manifest.yaml and .env, then
# `uv run factory run` should work end-to-end.
#
# Usage: bash scripts/bootstrap.sh
#
# Requires: a Debian/Ubuntu host with sudo. macOS is NOT supported (signal
# handling and shell expectations in the codebase target POSIX/Linux).

set -euo pipefail

# Base model pulled from ollama registry. The factory actually uses the 16k variant
# (created below) — the default ollama context of 4096 is too small for tool defs.
OPENCODE_BASE_MODEL="${OPENCODE_BASE_MODEL:-qwen3:8b}"
OPENCODE_DEFAULT_MODEL="${OPENCODE_BASE_MODEL}-16k"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

info()  { printf "\033[1;34m[bootstrap]\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m[bootstrap]\033[0m %s\n" "$*" >&2; }
error() { printf "\033[1;31m[bootstrap]\033[0m %s\n" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1; }

# ---------- OS guard ----------
case "$(uname -s)" in
  Linux) ;;
  *) error "This script targets Linux. macOS/Windows are not supported." ;;
esac

if ! need apt-get; then
  error "apt-get not found. This script assumes Debian/Ubuntu."
fi

# ---------- apt packages ----------
APT_MISSING=()
for pkg in git curl ca-certificates build-essential; do
  dpkg -s "$pkg" >/dev/null 2>&1 || APT_MISSING+=("$pkg")
done
if [ ${#APT_MISSING[@]} -gt 0 ]; then
  info "Installing apt packages: ${APT_MISSING[*]}"
  sudo apt-get update -y
  sudo apt-get install -y "${APT_MISSING[@]}"
else
  info "Apt prerequisites already present."
fi

# ---------- gh (GitHub CLI) ----------
if ! need gh; then
  info "Installing GitHub CLI (gh)..."
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
  sudo chmod a+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y gh
else
  info "gh already installed."
fi

# ---------- gitleaks ----------
if ! need gitleaks; then
  info "Installing gitleaks..."
  GITLEAKS_VERSION="8.21.2"
  ARCH="$(uname -m)"
  case "$ARCH" in
    x86_64) GL_ARCH="x64" ;;
    aarch64|arm64) GL_ARCH="arm64" ;;
    *) error "Unsupported arch for gitleaks: $ARCH" ;;
  esac
  TMP="$(mktemp -d)"
  curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GL_ARCH}.tar.gz" \
    | tar -xz -C "$TMP"
  sudo mv "$TMP/gitleaks" /usr/local/bin/gitleaks
  rm -rf "$TMP"
else
  info "gitleaks already installed."
fi

# ---------- uv (Python toolchain) ----------
if ! need uv; then
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installer writes to ~/.local/bin; ensure it's on PATH for this script
  export PATH="$HOME/.local/bin:$PATH"
else
  info "uv already installed."
fi

# ---------- Node (for codex CLI) ----------
if ! need node; then
  info "Installing Node.js (LTS) via NodeSource..."
  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
  sudo apt-get install -y nodejs
else
  info "node already installed ($(node --version))."
fi

# ---------- claude CLI ----------
if ! need claude; then
  info "Installing Claude Code CLI..."
  npm install -g @anthropic-ai/claude-code
else
  info "claude CLI already installed."
fi

# ---------- codex CLI ----------
if ! need codex; then
  info "Installing Codex CLI..."
  npm install -g @openai/codex
else
  info "codex CLI already installed."
fi

# ---------- ollama ----------
if ! need ollama; then
  info "Installing ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  info "ollama already installed ($(ollama --version 2>&1 | head -1))."
fi

# Start ollama if it isn't already serving
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  if systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
    info "Starting ollama systemd service..."
    sudo systemctl enable --now ollama
  else
    warn "ollama is not running. Start it with: ollama serve &"
    warn "Skipping model pull until ollama is reachable."
  fi
fi

# ---------- pull base coding model + create 16k-context variant ----------
if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OPENCODE_BASE_MODEL"; then
    info "Model $OPENCODE_BASE_MODEL already pulled."
  else
    info "Pulling $OPENCODE_BASE_MODEL (~5 GB, this may take a few minutes)..."
    ollama pull "$OPENCODE_BASE_MODEL"
  fi

  # Tool calling silently fails on ollama's default 4096 context. Build a 16k variant.
  if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OPENCODE_DEFAULT_MODEL"; then
    info "Variant $OPENCODE_DEFAULT_MODEL already exists."
  else
    info "Creating $OPENCODE_DEFAULT_MODEL with num_ctx=16384..."
    TMP_MF="$(mktemp)"
    printf "FROM %s\nPARAMETER num_ctx 16384\n" "$OPENCODE_BASE_MODEL" > "$TMP_MF"
    ollama create "$OPENCODE_DEFAULT_MODEL" -f "$TMP_MF"
    rm -f "$TMP_MF"
  fi
fi

# ---------- opencode ----------
if ! need opencode; then
  info "Installing opencode..."
  curl -fsSL https://opencode.ai/install | bash
  # opencode installer adds ~/.opencode/bin to PATH via shell rc; surface it here too
  export PATH="$HOME/.opencode/bin:$PATH"
fi

# ---------- opencode.json (point at ollama) ----------
OC_CFG_DIR="$HOME/.config/opencode"
OC_CFG="$OC_CFG_DIR/opencode.json"
if [ ! -f "$OC_CFG" ]; then
  info "Writing $OC_CFG..."
  mkdir -p "$OC_CFG_DIR"
  cat > "$OC_CFG" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (local)",
      "options": { "baseURL": "http://localhost:11434/v1" },
      "models": {
        "$OPENCODE_DEFAULT_MODEL": {
          "name": "$OPENCODE_DEFAULT_MODEL",
          "tools": true
        }
      }
    }
  }
}
EOF
else
  info "$OC_CFG already exists (left untouched)."
fi

# ---------- factory: uv sync ----------
cd "$REPO_ROOT"
info "Syncing factory Python deps (uv sync)..."
uv sync

# ---------- manifest.yaml stub ----------
if [ ! -f "$REPO_ROOT/manifest.yaml" ]; then
  info "Copying manifest.example.yaml → manifest.yaml (EDIT THIS)."
  cp "$REPO_ROOT/manifest.example.yaml" "$REPO_ROOT/manifest.yaml"
fi

# ---------- .env stub ----------
if [ ! -f "$REPO_ROOT/.env" ]; then
  info "Creating .env stub (FILL IN VALUES)."
  cat > "$REPO_ROOT/.env" <<'EOF'
LINEAR_API_KEY=
# Optional: override the default local model used by the opencode provider.
# OPENCODE_MODEL=ollama/qwen2.5-coder:14b
EOF
fi

# ---------- next steps ----------
cat <<EOF

$(info "Bootstrap finished. Remaining manual steps:")

  1.  gh auth login                # GitHub CLI auth
  2.  claude login                 # Claude Code OAuth (browser)
  3.  codex login                  # Codex OAuth (browser)
  4.  edit manifest.yaml           # Set repos[].github and local_path
  5.  edit .env                    # Paste LINEAR_API_KEY
  6.  uv run factory run --help    # Verify everything wired up

EOF
