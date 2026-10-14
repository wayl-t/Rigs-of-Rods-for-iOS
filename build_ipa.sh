#!/bin/bash
# Builds an UNSIGNED .ipa for sideloading.
# AltStore / SideStore re-sign it with your own Apple ID, so no paid account and
# no Mac are required: the GitHub Actions workflow calls this same script.
#
#   ./build_ipa.sh            free Apple ID  (normal memory limit)
#   ./build_ipa.sh --paid     paid account   (asks for the extra-memory entitlements)
#
# Needs: macOS + Xcode + xcodegen  (`brew install xcodegen`)
set -euo pipefail

ENTITLEMENTS=App/Free.entitlements
[ "${1:-}" = "--paid" ] && ENTITLEMENTS=App/Full.entitlements

cd "$(dirname "$0")/app"
JOBS="${JOBS:-$(sysctl -n hw.ncpu)}"
mkdir -p Build

echo "==> Generating the Xcode project"
xcodegen generate

echo "==> Building (Release, iphoneos, arm64)"
xcodebuild -project RoRNative.xcodeproj -scheme RoRNative \
  -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath Build/Release -jobs "$JOBS" \
  CODE_SIGNING_ALLOWED=NO CODE_SIGN_ENTITLEMENTS="$ENTITLEMENTS" \
  build > Build/xcodebuild.log 2>&1 || {
    grep -E 'error:' Build/xcodebuild.log | head -30
    echo "Build failed. Full log: app/Build/xcodebuild.log"
    exit 1
  }
grep -E 'BUILD SUCCEEDED' Build/xcodebuild.log

APP=Build/Release/Build/Products/Release-iphoneos/RoRNative.app

# Ad-hoc signature carrying the entitlements we WANT. This is what tells
# AltStore/SideStore which entitlements to request when they re-sign with your Apple ID.
echo "==> Ad-hoc signing with $ENTITLEMENTS"
codesign --force --sign - --entitlements "$ENTITLEMENTS" --generate-entitlement-der "$APP"

echo "==> Packing the IPA"
STAGE=Build/IPA
rm -rf "$STAGE"
mkdir -p "$STAGE/Payload"
ditto "$APP" "$STAGE/Payload/RoRNative.app"
(cd "$STAGE" && zip -qry ../../../RoRNative.ipa Payload)

echo "Done: $(cd ../../.. && pwd)/RoRNative.ipa"
