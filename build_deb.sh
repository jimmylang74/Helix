#!/usr/bin/env bash
# Build a Debian/Ubuntu .deb package for Helix.
#
# Output: ./dist/helix_<version>_<arch>.deb
#
# Usage:
#   ./build_deb.sh
#   HELIX_VERSION=1.2.3 ./build_deb.sh
#   HELIX_BUNDLE_WHEELS=0 ./build_deb.sh   # do not bundle Python wheels
#
# Paths used by the packaged instance (all separate from the existing checkout):
#   Application:  /opt/helix
#   Data / venv:  /var/lib/helix
#   Logs:         /var/log/helix
#   Unit file:    /lib/systemd/system/Helix.service
#
# An admin-provided /etc/systemd/system/Helix.service always takes precedence
# over the package unit, so an already running Helix.service is never replaced.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VERSION="${HELIX_VERSION:-1.0.0}"
ARCH="${HELIX_ARCH:-$(dpkg --print-architecture)}"
BUNDLE_WHEELS="${HELIX_BUNDLE_WHEELS:-1}"

PKG_NAME="helix"
APP_DIR="/opt/helix"
DATA_DIR="/var/lib/helix"
LOG_DIR="/var/log/helix"
VENV_DIR="${DATA_DIR}/venv"
WHEELS_DIR="/usr/share/helix/wheels"

DIST_DIR="${SCRIPT_DIR}/dist"
DEB_FILE="${DIST_DIR}/${PKG_NAME}_${VERSION}_${ARCH}.deb"

for tool in dpkg-deb fakeroot python3 pip3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Error: required tool not found: $tool" >&2
        exit 1
    fi
done

mkdir -p /tmp/opencode "$DIST_DIR"
BUILD_ROOT="$(mktemp -d /tmp/opencode/helix-deb.XXXXXX)"
trap 'rm -rf "$BUILD_ROOT"' EXIT

PKG_STAGE="${BUILD_ROOT}/${PKG_NAME}_${VERSION}_${ARCH}"
APP_STAGE="${PKG_STAGE}${APP_DIR}"
DEBIAN_DIR="${PKG_STAGE}/DEBIAN"
WHEELS_STAGE="${PKG_STAGE}${WHEELS_DIR}"

mkdir -p "$DEBIAN_DIR" "$APP_STAGE" "$WHEELS_STAGE"
mkdir -p "${PKG_STAGE}/lib/systemd/system" "${PKG_STAGE}/etc/logrotate.d"

# ── Application payload ──────────────────────────────────────────────────────
COPY_ITEMS=(
    Helix.py Helix.md Helix-cli.py Helix-cli.sh
    README.md README_EN.md requirements.txt
    ai_engine HelixCore modules mcp plugins web design
)

for item in "${COPY_ITEMS[@]}"; do
    if [[ ! -e "${SCRIPT_DIR}/${item}" ]]; then
        echo "Error: expected project item not found: ${item}" >&2
        exit 1
    fi
    cp -a "${SCRIPT_DIR}/${item}" "$APP_STAGE/"
done

# Drop developer/runtime artifacts; runtime caches are recreated on first start.
find "$APP_STAGE" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$APP_STAGE" -type f \( \
    -name '*.pyc' -o -name '*.pyo' -o -name '*.log' -o -name '.DS_Store' \
\) -delete
rm -rf "${APP_STAGE}/mcp/codegraph/node"
rm -rf "${APP_STAGE}/mcp/codegraph"/.node-extract.*

# Fresh, writable runtime directories. Existing project data is never copied.
mkdir -p "${APP_STAGE}/db" "${APP_STAGE}/output" \
    "${APP_STAGE}/download" "${APP_STAGE}/.codegraph"

# Keep log files out of the application directory and make them logrotatable.
for log_file in debugout.log llm_engine.log llm_engine_cron.log llm_engine_wechat.log; do
    ln -s "${LOG_DIR}/${log_file}" "${APP_STAGE}/${log_file}"
done

# Package a deliberately minimal config. Machine-specific endpoints, API keys,
# and all external MCP servers from the development checkout are not shipped.
# ConfigManager fills the remaining sections with built-in defaults on first run.
cat > "${APP_STAGE}/Helix.json" <<'JSON'
{
  "server": {
    "debug": false,
    "proxy": ""
  },
  "llm": {
    "provider": "ollama_native",
    "model": "qwen2.5:7b",
    "endpoint": "http://localhost:11434",
    "api_key": ""
  }
}
JSON

# weather_mcp.py still uses mcp.server.fastmcp, which was renamed in mcp 2.x.
# The source requirements say mcp>=1.26, so constrain the packaged instance
# without modifying the source file.
python3 - "$APP_STAGE/requirements.txt" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    content = fh.read()
content, count = re.subn(
    r'(?m)^mcp>=1\.26\s*$', 'mcp>=1.26,<2', content
)
if count == 0:
    print("Warning: mcp requirement line not found; weather MCP may break", file=sys.stderr)
with open(path, "w", encoding="utf-8") as fh:
    fh.write(content)
PY

# ── Python dependencies (bundled wheels => offline installation) ─────────────
if [[ "$BUNDLE_WHEELS" == "1" ]]; then
    echo "Downloading Python wheels into the package..."
    python3 -m pip download --disable-pip-version-check --only-binary=:all: \
        --dest "$WHEELS_STAGE" -r "$APP_STAGE/requirements.txt"
else
    echo "Wheel bundling disabled; postinst will install dependencies from the network."
    printf '%s\n' \
        'Wheels were not bundled. postinst will use pip with network access.' \
        > "${WHEELS_STAGE}/README"
fi

# ── Debian metadata ──────────────────────────────────────────────────────────
INSTALLED_SIZE="$(du -sk "$PKG_STAGE" | awk '{print $1}')"

cat > "${DEBIAN_DIR}/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Installed-Size: ${INSTALLED_SIZE}
Depends: adduser, ca-certificates, python3 (>= 3.10), python3-venv, systemd, tar, xz-utils
Maintainer: Helix Maintainers <helix@localhost>
Description: Helix AI Agent
 Self-contained Helix AI Agent service with its own Python virtual environment.
 .
 Installs the application under ${APP_DIR}, runtime data under ${DATA_DIR},
 logs under ${LOG_DIR}, and registers the systemd unit Helix.service.
 An existing /etc/systemd/system/Helix.service remains authoritative.
EOF

printf '%s\n' "${APP_DIR}/Helix.json" "/etc/logrotate.d/helix" > "${DEBIAN_DIR}/conffiles"

cat > "${PKG_STAGE}/lib/systemd/system/Helix.service" <<'EOF'
[Unit]
Description=Helix AI Agent (deb package)
Documentation=file:/opt/helix/README.md
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
User=helix
Group=helix
WorkingDirectory=/opt/helix
Environment=PATH=/var/lib/helix/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=PYTHONUNBUFFERED=1
ExecStart=/var/lib/helix/venv/bin/python /opt/helix/Helix.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > "${PKG_STAGE}/etc/logrotate.d/helix" <<'EOF'
/var/log/helix/*.log {
    daily
    rotate 14
    copytruncate
    compress
    missingok
    notifempty
}
EOF

cat > "${DEBIAN_DIR}/postinst" <<'EOF'
#!/bin/sh
set -e

APP_DIR=/opt/helix
DATA_DIR=/var/lib/helix
LOG_DIR=/var/log/helix
VENV_DIR=${DATA_DIR}/venv
WHEELS_DIR=/usr/share/helix/wheels
SERVICE=Helix.service
ETC_UNIT=/etc/systemd/system/${SERVICE}
MARKER=${DATA_DIR}/package-managed

case "$1" in
    configure)
        if ! getent passwd helix >/dev/null 2>&1; then
            adduser --system --group --home "${DATA_DIR}" --no-create-home \
                --disabled-login --gecos "Helix AI Agent" helix >/dev/null
        fi

        mkdir -p "${DATA_DIR}" "${LOG_DIR}" \
            "${APP_DIR}/db" "${APP_DIR}/output" \
            "${APP_DIR}/download" "${APP_DIR}/.codegraph"
        chown helix:helix "${DATA_DIR}" "${LOG_DIR}" \
            "${APP_DIR}/db" "${APP_DIR}/output" \
            "${APP_DIR}/download" "${APP_DIR}/.codegraph" \
            "${APP_DIR}/mcp/codegraph"
        chmod 0750 "${DATA_DIR}"
        chmod 0755 "${LOG_DIR}"

        # Config is edited by the web UI at runtime; keep ownership on upgrades.
        dpkg-statoverride --update --add helix helix 0644 "${APP_DIR}/Helix.json" >/dev/null 2>&1 || true
        chown helix:helix "${APP_DIR}/Helix.json"

        if [ ! -x "${VENV_DIR}/bin/python" ]; then
            python3 -m venv "${VENV_DIR}"
        fi
        if find "${WHEELS_DIR}" -maxdepth 1 -name '*.whl' -print -quit 2>/dev/null | grep -q .; then
            PIP_DISABLE_PIP_VERSION_CHECK=1 "${VENV_DIR}/bin/pip" install \
                --no-index --find-links="${WHEELS_DIR}" \
                --no-warn-script-location -r "${APP_DIR}/requirements.txt"
        else
            PIP_DISABLE_PIP_VERSION_CHECK=1 "${VENV_DIR}/bin/pip" install \
                --no-warn-script-location -r "${APP_DIR}/requirements.txt"
        fi
        chown -R helix:helix "${VENV_DIR}"

        systemctl daemon-reload >/dev/null 2>&1 || true

        # /etc/systemd/system always wins over /lib/systemd/system. If an admin
        # unit already exists, do not enable/start/stop anything for this package.
        if [ -f "${ETC_UNIT}" ]; then
            echo "Helix: existing ${ETC_UNIT} detected; package unit installed but not enabled." >&2
            rm -f "${MARKER}"
        else
            touch "${MARKER}"
            systemctl enable "${SERVICE}" >/dev/null 2>&1 || true
            if [ "${HELIX_NO_START:-0}" != "1" ]; then
                if systemctl is-active --quiet "${SERVICE}" 2>/dev/null; then
                    echo "Helix: ${SERVICE} is already active; skipping start." >&2
                elif command -v ss >/dev/null 2>&1 && \
                     ss -ltn 2>/dev/null | grep -Eq ':11555|:11556|:8004'; then
                    echo "Helix: a required port is already in use; skipping start." >&2
                else
                    systemctl start "${SERVICE}" >/dev/null 2>&1 || true
                fi
            fi
        fi
        ;;
esac

#DEBHELPER#
EOF

cat > "${DEBIAN_DIR}/prerm" <<'EOF'
#!/bin/sh
set -e

SERVICE=Helix.service
DATA_DIR=/var/lib/helix
MARKER=${DATA_DIR}/package-managed
ETC_UNIT=/etc/systemd/system/${SERVICE}

if [ "$1" = "remove" ] && [ -f "${MARKER}" ] && [ ! -f "${ETC_UNIT}" ]; then
    systemctl stop "${SERVICE}" >/dev/null 2>&1 || true
    systemctl disable "${SERVICE}" >/dev/null 2>&1 || true
fi

#DEBHELPER#
EOF

cat > "${DEBIAN_DIR}/postrm" <<'EOF'
#!/bin/sh
set -e

SERVICE=Helix.service
DATA_DIR=/var/lib/helix
LOG_DIR=/var/log/helix
MARKER=${DATA_DIR}/package-managed
ETC_UNIT=/etc/systemd/system/${SERVICE}

case "$1" in
    remove|purge)
        rm -f "${MARKER}"
        if [ ! -f "${ETC_UNIT}" ]; then
            systemctl daemon-reload >/dev/null 2>&1 || true
        fi
        if [ "$1" = "purge" ]; then
            dpkg-statoverride --remove /opt/helix/Helix.json >/dev/null 2>&1 || true
            rm -rf "${DATA_DIR}" "${LOG_DIR}"
        fi
        ;;
esac

#DEBHELPER#
EOF

chmod 0755 "${DEBIAN_DIR}/postinst" "${DEBIAN_DIR}/prerm" "${DEBIAN_DIR}/postrm"
find "${PKG_STAGE}" -type d -exec chmod 0755 {} +
find "${PKG_STAGE}" -type f -exec chmod 0644 {} +
chmod 0755 "${DEBIAN_DIR}/postinst" "${DEBIAN_DIR}/prerm" "${DEBIAN_DIR}/postrm"
chmod 0755 "${APP_STAGE}/Helix.py" "${APP_STAGE}/Helix-cli.py" \
    "${APP_STAGE}/Helix-cli.sh" "${APP_STAGE}/mcp/codegraph/bin/codegraph"
chmod 0644 "${DEBIAN_DIR}/conffiles"

# ── Build ────────────────────────────────────────────────────────────────────
echo "Building ${DEB_FILE} ..."
fakeroot dpkg-deb --build "$PKG_STAGE" "$DEB_FILE"

dpkg-deb --info "$DEB_FILE" >/dev/null
dpkg-deb --contents "$DEB_FILE" >/dev/null

echo "Done: ${DEB_FILE}"
echo "Install with: sudo dpkg -i ${DEB_FILE}"
echo "Existing /etc/systemd/system/Helix.service is left untouched by this package."
