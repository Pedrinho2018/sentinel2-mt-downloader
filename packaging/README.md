# Empacotamento Linux

O workflow `.github/workflows/packages.yml` gera um executável autocontido com
PyInstaller. A GUI PySide6 é a interface principal desses artefatos; TUI e CLI
permanecem incluídas no mesmo executável. O workflow produz os formatos:

- Debian/Ubuntu: `.deb`;
- Fedora/RHEL/openSUSE: `.rpm`;
- Arch Linux: `.pkg.tar.zst` e `PKGBUILD`;
- binário Linux x86_64 e `SHA256SUMS`.

## Publicação

O código declara a versão em `src/sentinel2_mt/__init__.py`. A tag deve usar a
mesma versão com o prefixo `v`:

```bash
git tag -a v2.0.0 -m "release: v2.0.0"
git push origin v2.0.0
```

O push da tag inicia o workflow e publica ou atualiza a GitHub Release. Uma
execução manual em **Actions → Pacotes Linux → Run workflow** gera os artefatos
para teste, mas não cria uma Release.

## Instalação dos artefatos

```bash
# Debian/Ubuntu
sudo apt install ./sentinel2-mt-downloader_2.0.0_amd64.deb

# Fedora/RHEL
sudo dnf install ./sentinel2-mt-downloader-2.0.0-1.x86_64.rpm

# Arch Linux
sudo pacman -U ./sentinel2-mt-downloader-bin-2.0.0-1-x86_64.pkg.tar.zst
```

O `PKGBUILD` publicado também pode ser colocado em uma pasta vazia e instalado
com `makepkg -si`.

## Execução

Abra **Sentinel-2 MT Downloader** pelo menu de aplicativos ou use:

```bash
sentinel2-mt                  # GUI principal
sentinel2-mt --gui            # GUI explícita
sentinel2-mt --tui            # interface de terminal
sentinel2-mt --cli --help     # automação por linha de comando
```

O atalho desktop não abre uma janela de terminal. Para usar a TUI, abra um
terminal e execute `sentinel2-mt --tui`.

Os pacotes instalam um lançador de compatibilidade que prioriza a `libstdc++`
do sistema e desativa a aceleração do QtWebEngine. Isso evita conflitos entre
as bibliotecas incluídas pelo PyInstaller e drivers gráficos mais recentes.

## Build local

Instale as dependências da GUI e do build antes de gerar
`dist/sentinel2-mt`. Depois, os pacotes DEB e RPM podem ser criados com:

```bash
python -m pip install -r requirements-gui.txt -r requirements-build.txt
python -m PyInstaller --noconfirm --clean packaging/sentinel2-mt.spec
packaging/build_linux_packages.sh 2.0.0
```

São necessários `dpkg-deb` e `rpmbuild`. O pacote Arch é construído no workflow
dentro da imagem `archlinux:base-devel`.
