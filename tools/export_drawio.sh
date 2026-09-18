#!/usr/bin/env bash
# .drawio → PNG（drawio 本体で書き出す。図の正は .drawio 側）
#   使い方: wsl -e bash /mnt/c/.../tools/export_drawio.sh
set -e
DIR="/mnt/c/Users/sons/Downloads/aws-test/BrightStar_Line_System/docs/architecture"
cd "$DIR"
sudo service docker start >/dev/null 2>&1 || true
sudo chmod 666 /var/run/docker.sock 2>/dev/null || true
docker run --rm -v "$DIR:/data" rlespinasse/drawio-export \
  -f png --scale 2 --remove-page-suffix . 2>&1 | tail -6
if [ -d "$DIR/export" ]; then
  cp -f "$DIR"/export/*.png "$DIR"/ 2>/dev/null || true
  rm -rf "$DIR/export"
fi
ls -la "$DIR"
