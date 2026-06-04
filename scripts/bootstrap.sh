#!/usr/bin/env bash
# Bootstrap ai_factory on a fresh macOS or Linux host.
#
# Idempotent: skips anything already installed. Safe to re-run.
# After this finishes, edit manifest.yaml and .env, then
# `uv run factory run` should work end-to-end.
#
# Usage: bash scripts/bootstrap.sh
#
# Supported:
#   - macOS (Apple Silicon or Intel) — installs via Homebrew
#   - Debian/Ubuntu Linux             — installs via apt
#
# Windows is not supported (signal handling in the factory targets POSIX).

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

# ---------- OS detection ----------
case "$(uname -s)" in
  Darwin) OS="darwin" ;;
  Linux)  OS="linux"  ;;
  *) error "Unsupported OS: $(uname -s). This script targets macOS or Linux." ;;
esac
info "Detected OS: $OS"

# ---------- package manager prerequisites ----------
if [ "$OS" = "darwin" ]; then
  if ! need brew; then
    error "Homebrew not found. Install from https://brew.sh first."
  fi
elif [ "$OS" = "linux" ]; then
  if ! need apt-get; then
    error "apt-get not found. This script's Linux path assumes Debian/Ubuntu."
  fi
  APT_MISSING=()
  for pkg in git curl ca-certificates build-essential; do
    dpkg -s "$pkg" >/dev/null 2>&1 || APT_MISSING+=("$pkg")
  done
  if [ ${#APT_MISSING[@]} -gt 0 ]; then
    info "Installing apt prerequisites: ${APT_MISSING[*]}"
    sudo apt-get update -y
    sudo apt-get install -y "${APT_MISSING[@]}"
  fi
fi

# ---------- gh (GitHub CLI) ----------
if ! need gh; then
  if [ "$OS" = "darwin" ]; then
    info "Installing gh via Homebrew..."
    brew install gh
  else
    info "Installing gh via apt repo..."
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
    sudo chmod a+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
    sudo apt-get update -y
    sudo apt-get install -y gh
  fi
else
  info "gh already installed."
fi

# ---------- gitleaks ----------
if ! need gitleaks; then
  if [ "$OS" = "darwin" ]; then
    info "Installing gitleaks via Homebrew..."
    brew install gitleaks
  else
    info "Installing gitleaks from GitHub release..."
    GITLEAKS_VERSION="8.21.2"
    case "$(uname -m)" in
      x86_64)         GL_ARCH="x64"   ;;
      aarch64|arm64)  GL_ARCH="arm64" ;;
      *) error "Unsupported arch for gitleaks: $(uname -m)" ;;
    esac
    TMP="$(mktemp -d)"
    curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${GL_ARCH}.tar.gz" \
      | tar -xz -C "$TMP"
    sudo mv "$TMP/gitleaks" /usr/local/bin/gitleaks
    rm -rf "$TMP"
  fi
else
  info "gitleaks already installed."
fi

# ---------- uv (Python toolchain) ----------
if ! need uv; then
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installer adds ~/.local/bin to PATH via shell rc; surface it here too
  export PATH="$HOME/.local/bin:$PATH"
else
  info "uv already installed."
fi

# ---------- Node (for codex CLI) ----------
if ! need node; then
  if [ "$OS" = "darwin" ]; then
    info "Installing Node.js via Homebrew..."
    brew install node
  else
    info "Installing Node.js (LTS) via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y nodejs
  fi
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
  if [ "$OS" = "darwin" ]; then
    info "Installing ollama via Homebrew..."
    brew install ollama
  else
    info "Installing ollama via curl installer..."
    curl -fsSL https://ollama.com/install.sh | sh
  fi
else
  info "ollama already installed ($(ollama --version 2>&1 | head -1))."
fi

# Start ollama if it isn't already serving
if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  if [ "$OS" = "darwin" ]; then
    info "Starting ollama via Homebrew services..."
    brew services start ollama || true
    # brew services takes a moment to actually bring the daemon up
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && break
      sleep 1
    done
  elif systemctl list-unit-files 2>/dev/null | grep -q '^ollama\.service'; then
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
# OPENCODE_MODEL=ollama/qwen3:8b-16k
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
