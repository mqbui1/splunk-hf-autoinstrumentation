#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CLASSES_DIR="$SCRIPT_DIR/target/classes"
JAR_OUT="$SCRIPT_DIR/../bootstrap-agent.jar"

echo "Building Splunk Auto-Instrumentation Bootstrap Agent..."

mkdir -p "$CLASSES_DIR"

javac --release 11 \
    -d "$CLASSES_DIR" \
    "$SCRIPT_DIR/src/com/splunk/autoinstr/BootstrapAgent.java"

jar --create \
    --file "$JAR_OUT" \
    --manifest "$SCRIPT_DIR/META-INF/MANIFEST.MF" \
    -C "$CLASSES_DIR" .

echo "Built: $JAR_OUT"
