#!/bin/bash
# Assembles the Splunk TA into a distributable .spl file.
#
# Usage:
#   bash package.sh [--version 1.0.0]
#
# Output:
#   dist/hf_autoinstrumentation-<version>.spl
#
# Requirements:
#   python3, pip, javac+jar (for bootstrap agent rebuild), tar
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VERSION="${1:-1.0.0}"
APP_NAME="hf_autoinstrumentation"
BUILD_DIR="$SCRIPT_DIR/.build/$APP_NAME"
DIST_DIR="$SCRIPT_DIR/dist"

echo "=== Splunk TA packager: $APP_NAME v$VERSION ==="

# ── 1. Clean build dir ───────────────────────────────────────────────────────
rm -rf "$BUILD_DIR"
mkdir -p \
    "$BUILD_DIR/bin/autoinstrumentation" \
    "$BUILD_DIR/bin/lib" \
    "$BUILD_DIR/default" \
    "$BUILD_DIR/README" \
    "$BUILD_DIR/metadata" \
    "$DIST_DIR"

# ── 2. Copy TA skeleton files ────────────────────────────────────────────────
cp "$SCRIPT_DIR/app.conf"                          "$BUILD_DIR/app.conf"
cp "$SCRIPT_DIR/bin/hf_autoinstrumentation.py"     "$BUILD_DIR/bin/"
cp "$SCRIPT_DIR/default/inputs.conf"               "$BUILD_DIR/default/"
cp "$SCRIPT_DIR/README/inputs.conf.spec"           "$BUILD_DIR/README/"
cp "$SCRIPT_DIR/metadata/default.meta"             "$BUILD_DIR/metadata/"

# Stamp version into app.conf
sed -i.bak "s/^version = .*/version = $VERSION/" "$BUILD_DIR/app.conf" && rm -f "$BUILD_DIR/app.conf.bak"

# ── 3. Copy autoinstrumentation Python package ───────────────────────────────
echo "Copying autoinstrumentation package..."
for f in __init__.py agent_manager.py config.py daemon.py discovery.py \
          injector.py jvm_attach.py state.py; do
    cp "$REPO_ROOT/autoinstrumentation/$f" "$BUILD_DIR/bin/autoinstrumentation/$f"
done

# ── 4. Build bootstrap agent JAR ────────────────────────────────────────────
echo "Building bootstrap-agent.jar..."
bash "$REPO_ROOT/bootstrap-agent/build.sh"
cp "$REPO_ROOT/bootstrap-agent.jar" "$BUILD_DIR/bin/bootstrap-agent.jar"

# ── 5. Vendor Python dependencies into bin/lib ──────────────────────────────
echo "Installing dependencies into bin/lib/..."
# Use python3 -m pip for portability (handles venvs and system Pythons)
python3 -m pip install psutil \
    --target "$BUILD_DIR/bin/lib" \
    --index-url https://pypi.org/simple/ \
    --quiet --no-compile

# ── 6. Set executable bit on entry point ────────────────────────────────────
chmod +x "$BUILD_DIR/bin/hf_autoinstrumentation.py"

# ── 7. Package as .spl (tar.gz with app dir at root) ────────────────────────
SPL="$DIST_DIR/${APP_NAME}-${VERSION}.spl"
echo "Creating $SPL ..."
tar -C "$SCRIPT_DIR/.build" -czf "$SPL" "$APP_NAME"

echo ""
echo "Done: $SPL"
echo ""
echo "Install on Heavy Forwarder:"
echo "  Copy $SPL to \$SPLUNK_HOME/etc/apps/"
echo "  tar -xzf ${APP_NAME}-${VERSION}.spl -C \$SPLUNK_HOME/etc/apps/"
echo "  Edit \$SPLUNK_HOME/etc/apps/$APP_NAME/local/inputs.conf"
echo "  \$SPLUNK_HOME/bin/splunk restart"
