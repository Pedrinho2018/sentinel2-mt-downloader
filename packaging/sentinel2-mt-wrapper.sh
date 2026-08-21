#!/bin/sh
set -eu

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

exec /usr/lib/sentinel2-mt/sentinel2-mt "$@"
