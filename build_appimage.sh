#!/bin/bash

set -e

APP_NAME="caesar-desktop"
APP_TITLE="CAESAR Desktop"

VERSION=$(cat version.txt)

OUTPUT_NAME="CAESAR_Desktop_v${VERSION}_"

echo "========================================="
echo " CAESAR Desktop"
echo " Version: $VERSION"
echo "========================================="

echo "========================================="
echo " Cleaning old build files"
echo "========================================="

rm -rf build
rm -rf dist
rm -rf AppDir
rm -f *.spec
rm -f *.AppImage

echo "=========================================="
echo " Generating version.py"
echo "=========================================="

echo "VERSION = \"$VERSION\"" > version.py

echo "=========================================="
echo " Building executable with PyInstaller"
echo "=========================================="

source venv/bin/activate

pyinstaller \
    --onefile \
    --windowed \
    --name "$APP_NAME" \
    main.py

echo "========================================="
echo " Creating AppDir"
echo "========================================="

mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

cp dist/$APP_NAME AppDir/usr/bin/

echo "========================================="
echo " Creating desktop file"
echo "========================================="

cat > AppDir/usr/share/applications/$APP_NAME.desktop << EOF
[Desktop Entry]
Type=Application
Name=$APP_TITLE
Exec=$APP_NAME
Icon=$APP_NAME
Categories=Network;
Terminal=false
EOF

echo "========================================="
echo " Copying icon"
echo "========================================="

cp RA0SMS_LOGO.png \
   AppDir/usr/share/icons/hicolor/256x256/apps/$APP_NAME.png

echo "========================================="
echo " Creating AppRun"
echo "========================================="

cat > AppDir/AppRun << 'EOF'
#!/bin/sh

HERE="$(dirname "$(readlink -f "$0")")"

exec "$HERE/usr/bin/caesar-desktop"
EOF

chmod +x AppDir/AppRun

echo "========================================="
echo " Building AppImage"
echo "========================================="

ARCH=x86_64 \
./tools/linuxdeploy-x86_64.AppImage \
    --appdir AppDir \
    --desktop-file AppDir/usr/share/applications/$APP_NAME.desktop \
    --output appimage

echo
echo "========================================="
echo " DONE"
echo "========================================="

APPIMAGE_FILE=$(ls *.AppImage | head -n1)

FINAL_NAME="${OUTPUT_NAME}-x86_64.AppImage"

mv "$APPIMAGE_FILE" "$FINAL_NAME"

mkdir -p Releases
mv "$FINAL_NAME" "Releases/$FINAL_NAME"

echo
echo "========================================="
echo " BUILD COMPLETE"
echo "========================================="

ls -lh "Releases/$FINAL_NAME"
