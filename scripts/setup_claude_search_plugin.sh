#!/bin/bash
# Install claude_search plugin for end-to-end testing.
# Run from hermes-agent repo root.
set -e

PLUGIN_DIR="$HOME/.hermes/plugins/claude_search"
SRC="$(dirname "$0")/claude_search_plugin"

mkdir -p "$PLUGIN_DIR"
cp "$SRC"/plugin.yaml "$PLUGIN_DIR/"
cp "$SRC"/__init__.py "$PLUGIN_DIR/"

echo "Plugin installed to $PLUGIN_DIR"
echo ""
echo "Enable in config.yaml:"
echo "  plugins:"
echo "    enabled:"
echo "      - Claude Search"
echo ""
echo "Copy sample DB (or use import tool to create your own):"
echo "  cp scripts/sample_claude_sessions.db ~/.hermes/claude_sessions.db"
echo ""
echo "Test with:"
echo "  HERMES_CLAUDE_SEARCH=1 hermes chat -q 'What is Zorg'"
