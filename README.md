# AEMS Local Bridge Agent

A lightweight companion service that runs on `localhost` and provides REST API access to the local filesystem, enabling the [AEMS](https://github.com/artkula/aems) web application to read/write exam PDFs to a user-chosen folder.

## Installation

### pip (recommended for development)

```bash
pip install aems-agent
```

### Binary installers

Download pre-built installers from [Releases](https://github.com/artkula/aems-agent/releases):

| Platform | File | Notes |
|----------|------|-------|
| Windows | `aems-agent-setup.exe` | Installs to `%LOCALAPPDATA%\AEMS Agent`, adds Start Menu shortcut |
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

# Show the auth token
aems-agent token

# Set the exam storage directory
aems-agent set-path /path/to/exams

# Show config directory location
aems-agent config-dir
```

## Configuration

Config files are stored in a platform-specific directory:

| Platform | Path |
|----------|------|
| Windows | `%APPDATA%\AEMS\agent\` |
| macOS | `~/.config/aems/agent/` |
| Linux | `~/.config/aems/agent/` (or `$XDG_CONFIG_HOME/aems/agent/`) |

Files:
- `config.json` — storage path, port, allowed origins
- `auth_token` — bearer token for API authentication
- `agent.log` — rotating log file

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
pip install -e ".[dev]"
python -m pytest -v
```

## License

MIT
