#!/bin/bash
set -e

# Package Yutori skills as zip files for Claude Desktop upload.
# Each zip contains a named directory at root with SKILL.md inside.
# Strips frontmatter keys not allowed by Claude Desktop.
#
# Allowed keys: name, description, license, allowed-tools, compatibility, metadata
#
# Usage: ./scripts/package-claude-desktop.sh
# Output: dist/claude-desktop/*.zip

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SRC_DIR="$ROOT_DIR/skills"
OUT_DIR="$ROOT_DIR/dist/claude-desktop"

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/*.zip

# Strip non-allowed frontmatter keys from SKILL.md
# Keeps: name, description, license, allowed-tools, compatibility, metadata
strip_frontmatter() {
  python3 -c "
import sys, re

content = open(sys.argv[1]).read()
m = re.match(r'^---\n(.*?)\n---\n(.*)$', content, re.DOTALL)
if not m:
    print(content, end='')
    sys.exit(0)

fm_lines = m.group(1).split('\n')
body = m.group(2)

allowed = {'name', 'description', 'license', 'allowed-tools', 'compatibility', 'metadata'}
filtered = [l for l in fm_lines if l.split(':')[0].strip() in allowed]

print('---')
for l in filtered:
    print(l)
print('---')
print(body, end='')
" "$1"
}

package_skill() {
  local src="$1"
  local name="$2"
  local temp_dir
  temp_dir=$(mktemp -d)

  mkdir -p "$temp_dir/$name"
  strip_frontmatter "$SRC_DIR/$src/SKILL.md" > "$temp_dir/$name/SKILL.md"

  (cd "$temp_dir" && zip -r "$OUT_DIR/$name.zip" "$name/")
  rm -rf "$temp_dir"
  echo "  $name.zip"
}

package_skill "01-scout"            "yutori-scout"
package_skill "02-research"         "yutori-research"
package_skill "03-browse"           "yutori-browse"
package_skill "04-competitor-watch" "yutori-competitor-watch"
package_skill "05-api-monitor"      "yutori-api-monitor"

echo ""
echo "Done. Output: $OUT_DIR/"
ls -lh "$OUT_DIR"/*.zip
