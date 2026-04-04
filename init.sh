#!/bin/bash
# init.sh - Development environment initialization and smoke tests
# Run this at the start of every Claude Code session

set -e  # Exit on error

echo "=========================================="
echo "MemoryMate PhotoFlow - Development Init"
echo "=========================================="
echo ""

# 1. Show current location
echo "[1/6] Checking working directory..."
pwd
echo ""

# 2. Show git status
echo "[2/6] Checking git status..."
git status --short
echo ""
echo "Current branch:"
git branch --show-current
echo ""
echo "Recent commits:"
git log --oneline -5
echo ""

# 3. Check Python environment
echo "[3/6] Checking Python environment..."
if command -v python3 &> /dev/null; then
    echo "Python version: $(python3 --version)"
    echo "Python location: $(which python3)"
else
    echo "ERROR: Python 3 not found!"
    exit 1
fi
echo ""

# 4. Check required Python packages
echo "[4/6] Checking required packages..."
REQUIRED_PACKAGES=("PySide6" "Pillow" "numpy")
MISSING_PACKAGES=()

for package in "${REQUIRED_PACKAGES[@]}"; do
    if python3 -c "import $package" 2>/dev/null; then
        echo "  ✓ $package installed"
    else
        echo "  ✗ $package MISSING"
        MISSING_PACKAGES+=("$package")
    fi
done

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    echo ""
    echo "WARNING: Missing packages: ${MISSING_PACKAGES[*]}"
    echo "Install with: pip install ${MISSING_PACKAGES[*]}"
    echo ""
fi
echo ""

# 5. Check key files exist
echo "[5/6] Checking project structure..."
KEY_FILES=(
    "main.py"
    "reference_db.py"
    "layouts/google_layout.py"
    "ui/accordion_sidebar/__init__.py"
    "ui/accordion_sidebar/people_section.py"
    "FeatureList.json"
    "ClaudeProgress.txt"
)

for file in "${KEY_FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "  ✓ $file"
    else
        echo "  ✗ $file MISSING"
    fi
done
echo ""

# 6. Show scaffold files status
echo "[6/6] Scaffolding files:"
if [ -f "FeatureList.json" ]; then
    echo "  ✓ FeatureList.json ($(grep -c '"status"' FeatureList.json) features tracked)"
fi
if [ -f "ClaudeProgress.txt" ]; then
    echo "  ✓ ClaudeProgress.txt (progress log ready)"
fi
if [ -f "init.sh" ]; then
    echo "  ✓ init.sh (this script)"
fi
echo ""

# Summary
echo "=========================================="
echo "Initialization Complete!"
echo "=========================================="
echo ""
echo "Quick commands:"
echo "  • View features:  cat FeatureList.json | python3 -m json.tool"
echo "  • View progress:  cat ClaudeProgress.txt"
echo "  • Run app:        python3 main.py"
echo "  • Run tests:      pytest tests/ (if tests exist)"
echo ""
echo "Ready to develop! 🚀"
echo ""
