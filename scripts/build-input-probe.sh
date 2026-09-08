#!/bin/zsh

set -euo pipefail

repo_path="${0:A:h:h}"
package_path="$repo_path/tools/YutoriInputProbe"
configuration="${1:-debug}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "Yutori Input Probe can only be built on macOS."
  exit 1
fi

if [[ "$configuration" != "debug" && "$configuration" != "release" ]]; then
  print -u2 "usage: $0 [debug|release]"
  exit 2
fi

swift build --package-path "$package_path" --configuration "$configuration" --product YutoriInputProbe >&2
binary_path="$(swift build --package-path "$package_path" --configuration "$configuration" --show-bin-path)/YutoriInputProbe"
app_path="$package_path/.build/YutoriInputProbe.app"

mkdir -p "$app_path/Contents/MacOS" "$app_path/Contents/Resources"
cp "$binary_path" "$app_path/Contents/MacOS/YutoriInputProbe"
cp "$package_path/Resources/Info.plist" "$app_path/Contents/Info.plist"
plutil -lint "$app_path/Contents/Info.plist" >&2
codesign --force --sign - --timestamp=none "$app_path" >&2

launch_services="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [[ -x "$launch_services" ]]; then
  "$launch_services" -f "$app_path"
fi

print "$app_path"
