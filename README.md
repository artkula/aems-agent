# AEMS Local Bridge Agent

A lightweight companion service that runs on `localhost` and provides REST API access to the local filesystem, enabling the [AEMS](https://github.com/artkula/aems) web app to read/write exam PDFs to a user-chosen folder.

## Installation

### pip (recommended for development)

```bash
pip install aems-agent
```

### Binary installers

Download pre-built installers from [Releases](https://github.com/artkula/aems-agent/releases):

| Platform | File | Notes |
|----------|------|-------|
| Windows | `aems-agent-setup.exe` | Installs to `%LOCALAPPDATA%\AEMS Agent` |
| macOS | `AEMS-Agent.dmg` | Drag to Applications |
| Linux | `aems-agent-linux.tar.gz` | Extract and run `./aems-agent run` |

## Usage

```bash
# Start the agent (default: http://127.0.0.1:61234)
aems-agent run

# Start with system tray icon
aems-agent run --tray

# Custom port/host
aems-agent run --port 9000 --host 0.0.0.0

# Enforce runtime license policy
aems-agent run --license-policy warn
aems-agent run --license-policy soft-block
aems-agent run --license-policy hard-block

# Show auth token
aems-agent token

# Set exam storage directory
aems-agent set-path /path/to/exams

# Show config directory location
aems-agent config-dir

# Store a license JWT token
aems-agent license-store "<jwt-token>"

# Validate token signature + claims + heartbeat
aems-agent license-check \
  --license-url https://license.domain.com \
  --issuer https://license.domain.com \
  --audience aems-agent
```

## Configuration

Config files are stored in a platform-specific directory:

| Platform | Path |
|----------|------|
| Windows | `%APPDATA%\AEMS\agent\` |
| macOS | `~/.config/aems/agent/` |
| Linux | `~/.config/aems/agent/` (or `$XDG_CONFIG_HOME/aems/agent/`) |

Files:
- `config.json` - storage path, port, allowed origins, license settings
- `auth_token` - bearer token for API authentication
- `license.jwt` - stored license token
- `agent.log` - rotating log file

Runtime license policy modes:
- `warn`: agent starts and logs validation failures.
- `soft-block`: agent starts in limited mode when license is invalid. Write operations (`PUT/DELETE /files/*`, `PUT /config/path`) are blocked until license becomes valid again.
- `hard-block`: startup fails and exits non-zero when license is invalid/revoked/grace-expired; runtime checks also terminate the process non-zero on hard-block failures.

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/status` | No | Alive check |
| GET | `/health` | Yes | Detailed health with disk info |
| GET/PUT | `/config/path` | Yes | Get/set storage path |
| GET | `/files/{assignment_id}` | Yes | List submissions |
| GET/PUT/DELETE | `/files/{aid}/{sid}` | Yes | Manage submission PDFs |
| GET/PUT | `/files/{aid}/{sid}/annotated` | Yes | Manage annotated PDFs |
| POST | `/pair/initiate` | No | Start browser pairing |
| POST | `/pair/complete` | No | Complete pairing |

## Development

```bash
git clone https://github.com/artkula/aems-agent.git
cd aems-agent
python -m pip install -e ".[dev]"
python -m pytest -v
```

## Release Trust and Verification

Release pipeline:
- `.github/workflows/build.yml`
- Windows tagged releases are Authenticode-signed.
- macOS tagged releases are code-signed, notarized, and stapled.
- `release-manifest.json` and `sha256sums.txt` are signed with cosign as supplemental integrity proof.

Verification examples:

Windows:
```powershell
Get-AuthenticodeSignature .\aems-agent-setup.exe | Format-List
```

macOS:
```bash
codesign --verify --deep --strict --verbose=2 "AEMS Agent.app"
spctl --assess --type open --context context:primary-signature --verbose=4 "AEMS-Agent.dmg"
```

Cosign manifest verification:
```bash
cosign verify-blob \
  --certificate release-manifest.pem \
  --signature release-manifest.sig \
  release-manifest.json
```

## License

MIT
