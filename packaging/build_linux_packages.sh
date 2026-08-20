#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${1:?Uso: build_linux_packages.sh VERSION [BINARIO] [SAIDA]}"
BINARY="${2:-$ROOT/dist/sentinel2-mt}"
OUT="${3:-$ROOT/release}"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Versão inválida: use o formato X.Y.Z" >&2
  exit 2
fi
if [[ ! -x "$BINARY" ]]; then
  echo "Binário não encontrado ou não executável: $BINARY" >&2
  exit 2
fi
for comando in dpkg-deb rpmbuild sha256sum; do
  command -v "$comando" >/dev/null || { echo "Comando obrigatório ausente: $comando" >&2; exit 2; }
done

WORK="$ROOT/.build/packages"
rm -rf "$WORK"
mkdir -p "$WORK/deb/DEBIAN" "$WORK/deb/usr/bin" "$WORK/deb/etc/sentinel2-mt"
mkdir -p "$WORK/deb/usr/share/applications" "$OUT"

install -m755 "$BINARY" "$WORK/deb/usr/bin/sentinel2-mt"
install -m644 "$ROOT/packaging/config.yaml" "$WORK/deb/etc/sentinel2-mt/config.yaml"
install -m644 "$ROOT/packaging/sentinel2-mt.desktop" "$WORK/deb/usr/share/applications/sentinel2-mt.desktop"

cat >"$WORK/deb/DEBIAN/control" <<EOF
Package: sentinel2-mt-downloader
Version: $VERSION
Section: science
Priority: optional
Architecture: amd64
Maintainer: Sentinel2 MT Maintainers
Description: Downloader e sincronizador de imagens Sentinel-2 de Mato Grosso
 Aplicação TUI para catalogar, baixar, processar e sincronizar imagens com Google Drive.
EOF

dpkg-deb --root-owner-group --build "$WORK/deb" "$OUT/sentinel2-mt-downloader_${VERSION}_amd64.deb"

RPMROOT="$WORK/rpmbuild"
mkdir -p "$RPMROOT"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
install -m755 "$BINARY" "$RPMROOT/SOURCES/sentinel2-mt"
install -m644 "$ROOT/packaging/config.yaml" "$RPMROOT/SOURCES/config.yaml"
install -m644 "$ROOT/packaging/sentinel2-mt.desktop" "$RPMROOT/SOURCES/sentinel2-mt.desktop"
cp "$ROOT/packaging/rpm/sentinel2-mt.spec" "$RPMROOT/SPECS/"
rpmbuild --define "_topdir $RPMROOT" --define "package_version $VERSION" -bb "$RPMROOT/SPECS/sentinel2-mt.spec"
cp "$RPMROOT/RPMS/x86_64/"*.rpm "$OUT/"

install -m755 "$BINARY" "$OUT/sentinel2-mt-linux-x86_64"
install -m644 "$ROOT/packaging/config.yaml" "$OUT/sentinel2-mt-config.yaml"
install -m644 "$ROOT/packaging/sentinel2-mt.desktop" "$OUT/sentinel2-mt.desktop"

BINARY_SHA256="$(sha256sum "$OUT/sentinel2-mt-linux-x86_64" | cut -d' ' -f1)"
CONFIG_SHA256="$(sha256sum "$OUT/sentinel2-mt-config.yaml" | cut -d' ' -f1)"
DESKTOP_SHA256="$(sha256sum "$OUT/sentinel2-mt.desktop" | cut -d' ' -f1)"
sed \
  -e "s/@VERSION@/$VERSION/g" \
  -e "s/@BINARY_SHA256@/$BINARY_SHA256/g" \
  -e "s/@CONFIG_SHA256@/$CONFIG_SHA256/g" \
  -e "s/@DESKTOP_SHA256@/$DESKTOP_SHA256/g" \
  "$ROOT/packaging/arch/PKGBUILD.in" >"$OUT/PKGBUILD"

echo "Pacotes DEB/RPM e PKGBUILD gerados em: $OUT"
