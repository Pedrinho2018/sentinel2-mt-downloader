Name:           sentinel2-mt-downloader
Version:        %{package_version}
Release:        1%{?dist}
Summary:        Downloader e sincronizador de imagens Sentinel-2 de Mato Grosso
License:        Proprietary
URL:            https://github.com/coldrenatinho/sentinel2-mt-downloader
BuildArch:      x86_64
Source0:        sentinel2-mt
Source1:        config.yaml
Source2:        sentinel2-mt.desktop

%description
Aplicação de terminal para catalogar, baixar, processar e sincronizar imagens
Sentinel-2 de Mato Grosso com o Google Drive.

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
