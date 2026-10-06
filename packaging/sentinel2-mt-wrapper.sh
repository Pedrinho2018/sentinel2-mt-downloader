#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
adjacent_binary="$script_dir/sentinel2-mt-linux-x86_64"
installed_binary=/usr/lib/sentinel2-mt/sentinel2-mt

if [ -f "$adjacent_binary" ] && [ -x "$adjacent_binary" ] && ! [ "$adjacent_binary" -ef "$0" ]; then
    binary=$adjacent_binary
elif [ -f "$installed_binary" ] && [ -x "$installed_binary" ] && ! [ "$installed_binary" -ef "$0" ]; then
    binary=$installed_binary
else
    echo "Erro: executável Sentinel-2 MT não encontrado." >&2
    exit 127
fi

# The PyInstaller bundle may contain an older libstdc++ than the host's
# graphics/video drivers. Prefer the system runtime for those drivers.
for system_libstdcpp in \
    /usr/lib/libstdc++.so.6 \
    /usr/lib64/libstdc++.so.6 \
    /usr/lib/x86_64-linux-gnu/libstdc++.so.6
do
    if [ -r "$system_libstdcpp" ]; then
        export LD_PRELOAD="$system_libstdcpp${LD_PRELOAD:+:$LD_PRELOAD}"
        break
    fi
done
export QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-gpu-compositing${QTWEBENGINE_CHROMIUM_FLAGS:+ $QTWEBENGINE_CHROMIUM_FLAGS}"

exec "$binary" "$@"
