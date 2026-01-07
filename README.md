# TMSU Explorer

A robust, Zotero-inspired Terminal User Interface (TUI) for the [TMSU](https://tmsu.org/) file tagging system. Built with Python and [Textual](https://textual.textualize.io/).

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)

## Overview

TMSU Explorer provides a modern, clean interface for managing file tags without leaving your terminal. It mimics the three-pane layout of Zotero:
1.  **Left Pane**: Navigation (File System & TMSU Queries) and Tag List.
2.  **Middle Pane**: File list showing path, size, and modification date.
3.  **Right Pane**: Inspector showing file metadata (via ExifTool) and an interactive tag editor.

## Features

* **Monolithic Design**: Single-file Python logic for easy deployment.
* **Crash Resistant**: Gracefully handles missing backend tools with configuration dialogs.
* **Deep Integration**:
    * **TMSU**: Add, remove, and filter by tags.
    * **ExifTool**: Live technical metadata inspection for selected files.
* **Keyboard Navigation**: Full keyboard support for efficiency.

## Requirements

* **Python 3.8+**
* **[TMSU](https://tmsu.org/)**: The file tagging backend.
* **[ExifTool](https://exiftool.org/)**: For extracting file metadata.

## Installation

1.  Clone the repository:
    ```bash
    git clone [https://github.com/YOUR_USERNAME/tmsu-explorer.git](https://github.com/YOUR_USERNAME/tmsu-explorer.git)
    cd tmsu-explorer
    ```

2.  Create a virtual environment (recommended):
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  Install dependencies:
    ```bash
    pip install textual
    ```

## Usage

Ensure `tmsu` and `exiftool` are installed and accessible in your system PATH.

Run the application:
```bash
python tmsu_explorer.py

```

*Note: If the tools are not found in your PATH, the application will prompt you to specify their locations on startup.*

## Key Bindings

| Key | Action |
| --- | --- |
| `F1` / `?` | Show Help |
| `F5` | Refresh View |
| `Ctrl+L` | Toggle Debug Log |
| `Ctrl+Q` | Quit |

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](https://www.google.com/search?q=LICENSE) file for details.
