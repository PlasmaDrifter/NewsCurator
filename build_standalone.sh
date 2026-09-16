#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building NewsCurator standalone binary with PyInstaller..."
pyinstaller --clean NewsCurator.spec

if [ -f "dist/NewsCurator" ]; then
    echo "Build successful: dist/NewsCurator"
    chmod +x dist/NewsCurator

    VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "latest")
    ARCH="linux-x86_64"
    RELEASE_DIR="dist/NewsCurator-${VERSION}-${ARCH}"

    rm -rf "$RELEASE_DIR"
    mkdir -p "$RELEASE_DIR"
    cp dist/NewsCurator "$RELEASE_DIR/"
    cp README.md "$RELEASE_DIR/" 2>/dev/null || true

    cd dist
    tar -czf "NewsCurator-${VERSION}-${ARCH}.tar.gz" "NewsCurator-${VERSION}-${ARCH}"
    zip -r "NewsCurator-${VERSION}-${ARCH}.zip" "NewsCurator-${VERSION}-${ARCH}"
    cd ..

    echo "Standalone packages generated in dist/:"
    echo "  - dist/NewsCurator-${VERSION}-${ARCH}.tar.gz"
    echo "  - dist/NewsCurator-${VERSION}-${ARCH}.zip"
else
    echo "Build failed: dist/NewsCurator not found"
    exit 1
fi
