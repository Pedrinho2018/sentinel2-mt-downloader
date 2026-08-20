# Empacotamento Linux

O workflow `.github/workflows/packages.yml` gera um executável autocontido com
PyInstaller e produz os formatos:

- Debian/Ubuntu: `.deb`;
- Fedora/RHEL/openSUSE: `.rpm`;
- Arch Linux: `.pkg.tar.zst` e `PKGBUILD`;
- binário Linux x86_64 e `SHA256SUMS`.

## Publicação

O código declara a versão em `src/sentinel2_mt/__init__.py`. A tag deve usar a
mesma versão com o prefixo `v`:

```bash
git tag -a v1.0.0 -m "release: v1.0.0"
git push origin v1.0.0
```

O push da tag inicia o workflow e publica ou atualiza a GitHub Release. Uma
execução manual em **Actions → Pacotes Linux → Run workflow** gera os artefatos
para teste, mas não cria uma Release.

## Instalação dos artefatos

```bash
# Debian/Ubuntu
sudo apt install ./sentinel2-mt-downloader_1.0.0_amd64.deb

# Fedora/RHEL
sudo dnf install ./sentinel2-mt-downloader-1.0.0-1.x86_64.rpm

# Arch Linux
sudo pacman -U ./sentinel2-mt-downloader-bin-1.0.0-1-x86_64.pkg.tar.zst
```

O `PKGBUILD` publicado também pode ser colocado em uma pasta vazia e instalado
com `makepkg -si`.

## Build local

Depois de gerar `dist/sentinel2-mt` com o PyInstaller, os pacotes DEB e RPM
podem ser criados com:

```bash
packaging/build_linux_packages.sh 1.0.0
```

São necessários `dpkg-deb` e `rpmbuild`. O pacote Arch é construído no workflow
dentro da imagem `archlinux:base-devel`.
