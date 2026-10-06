Name:           sentinel2-mt-downloader
Version:        %{package_version}
Release:        1%{?dist}
Summary:        Interface gráfica para imagens Sentinel-2 de Mato Grosso
License:        Proprietary
URL:            https://github.com/coldrenatinho/sentinel2-mt-downloader
BuildArch:      x86_64
Source0:        sentinel2-mt
Source1:        config.yaml
Source2:        sentinel2-mt.desktop
Requires:       glibc
Requires:       libglvnd-glx
Requires:       libxkbcommon-x11
Requires:       nss
Requires:       alsa-lib
Requires:       dbus-libs
Requires:       fontconfig
Requires:       xcb-util-cursor

%description
Aplicação gráfica para catalogar, baixar, processar e sincronizar imagens
Sentinel-2 de Mato Grosso com o Google Drive. Também oferece interfaces TUI e
CLI para uso no terminal e em automações.

%prep

%build

%install
install -Dm755 %{SOURCE0} %{buildroot}%{_bindir}/sentinel2-mt
install -Dm644 %{SOURCE1} %{buildroot}%{_sysconfdir}/sentinel2-mt/config.yaml
install -Dm644 %{SOURCE2} %{buildroot}%{_datadir}/applications/sentinel2-mt.desktop

%files
%{_bindir}/sentinel2-mt
%config(noreplace) %{_sysconfdir}/sentinel2-mt/config.yaml
%{_datadir}/applications/sentinel2-mt.desktop

%changelog
* Thu Aug 20 2026 Sentinel2 MT Maintainers - %{version}-1
- Pacote automatizado pelo GitHub Actions.
