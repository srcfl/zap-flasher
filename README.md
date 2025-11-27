# Sourceful Zap ESP32 Sequential Flasher

A standalone tool for flashing Sourceful Zap firmware to ESP32-C3 devices sequentially. Extracts serial numbers and public keys.

## Features
- 🚀 **Fast**: No flash erase by default (460800 baud)
- 📁 **Project Aware**: Flashes directly from IDF build directories
- 🔍 **Auto-detection**: Finds binary files and serial ports
- 📊 **Data extraction**: Captures device serial numbers and public keys to CSV/JSON
- 🔄 **Sequential**: Flash multiple devices one by one

## Installation

### macOS / Linux
```bash
# Install uv (recommended)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup
cd zap-flasher
uv sync
```

### Windows
```powershell
# Install uv (recommended)
powershell -c "irm https://astral.sh/uv/install.sh | iex"

# Setup
cd zap-flasher
uv sync
```

## Usage

### Flash from Project (Recommended)
Automatically finds binaries in `../[project_name]/build` or uses the provided directory path.
If no argument is provided, it looks for binaries in `default_fw/` or the current directory.

```bash
# Flash fw_controller project
uv run flasher.py --project fw_controller

# Flash from a specific directory
uv run flasher.py --dir ./my_release_v1

# Flash from default_fw/ (if exists)
uv run flasher.py

# With flash erase (safer)
uv run flasher.py --project fw_controller --erase
```

### Manual Usage
```bash
# Specify binary directory
uv run flasher.py --dir path/to/binaries

# Specify port
uv run flasher.py --project fw_controller --port /dev/cu.usbmodem101
```

## Process
1. **Connect** ESP32-C3 via USB
2. **Flash** - Tool flashes and resets
3. **Extract** - Captures serial/keys
4. **Save** - Appends to `flash_results_YYYYMMDD_HHMMSS.csv`
5. **Repeat** - Connect next device

## Output
- **CSV**: `flash_results_*.csv` (ecc_serial, public_keys)
- **JSON**: `flash_results_*.json` (full logs)

## Troubleshooting
- **Port busy**: Close other serial monitors
- **No output**: Try `--erase`
- **Linux**: Add user to `dialout` group

