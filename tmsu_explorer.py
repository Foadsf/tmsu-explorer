#!/usr/bin/env python3
"""
TMSU Explorer - A Zotero-style TUI for managing file tags with TMSU and ExifTool.

This monolithic application provides a three-pane interface for:
- Browsing files and TMSU queries (left sidebar)
- Viewing file lists (middle pane)
- Inspecting metadata and managing tags (right pane)

Dependencies:
    - textual: TUI framework
    - tmsu: CLI tagging tool (external)
    - exiftool: CLI metadata tool (external)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    DirectoryTree,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode

# =============================================================================
# Logging Configuration
# =============================================================================

LOG_FILE = Path(__file__).parent / "tmsu_tui.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("TMSU Explorer started")
logger.info("=" * 60)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class FileInfo:
    """Represents a file with its metadata."""

    path: Path
    name: str
    size: int = 0
    modified: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_path(cls, path: Path) -> FileInfo:
        """Create FileInfo from a Path object."""
        try:
            stat = path.stat()
            return cls(
                path=path,
                name=path.name,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )
        except OSError as e:
            logger.warning(f"Could not stat file {path}: {e}")
            return cls(path=path, name=path.name)


@dataclass
class ExifMetadata:
    """Container for EXIF metadata."""

    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Get a metadata value by key."""
        return self.data.get(key, default)


# =============================================================================
# Backend Wrapper Class
# =============================================================================


class Backend:
    """
    Handles all subprocess interactions with tmsu and exiftool.
    
    This class isolates the backend logic from the UI, providing clean
    methods for all external tool operations.
    """

    def __init__(self) -> None:
        """Initialize backend with tool paths."""
        self.tmsu_path: Optional[str] = None
        self.exiftool_path: Optional[str] = None
        self._detect_tools()

    def _detect_tools(self) -> None:
        """Detect tmsu and exiftool in system PATH."""
        self.tmsu_path = shutil.which("tmsu")
        self.exiftool_path = shutil.which("exiftool")

        if self.tmsu_path:
            logger.info(f"Found tmsu at: {self.tmsu_path}")
        else:
            logger.warning("tmsu not found in PATH")

        if self.exiftool_path:
            logger.info(f"Found exiftool at: {self.exiftool_path}")
        else:
            logger.warning("exiftool not found in PATH")

    @property
    def tmsu_available(self) -> bool:
        """Check if tmsu is available."""
        return self.tmsu_path is not None

    @property
    def exiftool_available(self) -> bool:
        """Check if exiftool is available."""
        return self.exiftool_path is not None

    def set_tmsu_path(self, path: str) -> bool:
        """Set custom tmsu path and verify it works."""
        if Path(path).is_file():
            self.tmsu_path = path
            logger.info(f"Set custom tmsu path: {path}")
            return True
        logger.error(f"Invalid tmsu path: {path}")
        return False

    def set_exiftool_path(self, path: str) -> bool:
        """Set custom exiftool path and verify it works."""
        if Path(path).is_file():
            self.exiftool_path = path
            logger.info(f"Set custom exiftool path: {path}")
            return True
        logger.error(f"Invalid exiftool path: {path}")
        return False

    def _run_command(
        self,
        cmd: list[str],
        cwd: Optional[Path] = None,
        timeout: int = 30,
    ) -> tuple[bool, str, str]:
        """
        Run a subprocess command safely.
        
        Args:
            cmd: Command and arguments as list
            cwd: Working directory
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (success, stdout, stderr)
        """
        cmd_str = " ".join(cmd)
        logger.debug(f"Running command: {cmd_str}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            logger.debug(f"Command exit code: {result.returncode}")

            if result.returncode == 0:
                return True, result.stdout.strip(), result.stderr.strip()
            else:
                logger.warning(f"Command failed: {result.stderr}")
                return False, result.stdout.strip(), result.stderr.strip()

        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {cmd_str}")
            return False, "", "Command timed out"
        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return False, "", f"Command not found: {e}"
        except Exception as e:
            logger.exception(f"Unexpected error running command: {e}")
            return False, "", str(e)

    # -------------------------------------------------------------------------
    # TMSU Operations
    # -------------------------------------------------------------------------

    def get_all_tags(self, cwd: Optional[Path] = None) -> tuple[bool, list[str]]:
        """
        Get all unique tags from the TMSU database.
        
        Returns:
            Tuple of (success, list of tag names)
        """
        if not self.tmsu_path:
            return False, []

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "tags"],
            cwd=cwd,
        )

        if success and stdout:
            tags = [t.strip() for t in stdout.splitlines() if t.strip()]
            logger.info(f"Retrieved {len(tags)} tags")
            return True, sorted(tags)

        if "no database" in stderr.lower():
            logger.info("No TMSU database found in current directory")
            return True, []

        return False, []

    def get_file_tags(
        self, file_path: Path, cwd: Optional[Path] = None
    ) -> tuple[bool, list[str]]:
        """
        Get tags for a specific file.
        
        Args:
            file_path: Path to the file
            cwd: Working directory for TMSU
            
        Returns:
            Tuple of (success, list of tags)
        """
        if not self.tmsu_path:
            return False, []

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "tags", str(file_path)],
            cwd=cwd,
        )

        if success and stdout:
            # Output format: "filename: tag1 tag2 tag3" or just "tag1 tag2 tag3"
            if ":" in stdout:
                parts = stdout.split(":", 1)
                if len(parts) > 1:
                    tags = parts[1].strip().split()
                else:
                    tags = []
            else:
                tags = stdout.strip().split()
            return True, tags

        return True, []  # No tags is valid

    def add_tag(
        self,
        file_path: Path,
        tag: str,
        cwd: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """
        Add a tag to a file.
        
        Args:
            file_path: Path to the file
            tag: Tag name to add
            cwd: Working directory for TMSU
            
        Returns:
            Tuple of (success, error message if failed)
        """
        if not self.tmsu_path:
            return False, "TMSU not available"

        # Sanitize tag name
        tag = tag.strip().replace(" ", "_")
        if not tag:
            return False, "Empty tag name"

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "tag", str(file_path), tag],
            cwd=cwd,
        )

        if success:
            logger.info(f"Added tag '{tag}' to {file_path}")
            return True, ""
        else:
            return False, stderr or "Unknown error"

    def remove_tag(
        self,
        file_path: Path,
        tag: str,
        cwd: Optional[Path] = None,
    ) -> tuple[bool, str]:
        """
        Remove a tag from a file.
        
        Args:
            file_path: Path to the file
            tag: Tag name to remove
            cwd: Working directory for TMSU
            
        Returns:
            Tuple of (success, error message if failed)
        """
        if not self.tmsu_path:
            return False, "TMSU not available"

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "untag", str(file_path), tag],
            cwd=cwd,
        )

        if success:
            logger.info(f"Removed tag '{tag}' from {file_path}")
            return True, ""
        else:
            return False, stderr or "Unknown error"

    def query_files(
        self,
        query: str,
        cwd: Optional[Path] = None,
    ) -> tuple[bool, list[Path]]:
        """
        Query files by tag expression.
        
        Args:
            query: TMSU query string (tag name or expression)
            cwd: Working directory for TMSU
            
        Returns:
            Tuple of (success, list of file paths)
        """
        if not self.tmsu_path:
            return False, []

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "files", query],
            cwd=cwd,
        )

        if success and stdout:
            files = [Path(f.strip()) for f in stdout.splitlines() if f.strip()]
            logger.info(f"Query '{query}' returned {len(files)} files")
            return True, files

        return True, []  # Empty result is valid

    def get_untagged_files(
        self, cwd: Optional[Path] = None
    ) -> tuple[bool, list[Path]]:
        """
        Get all untagged files in the TMSU database.
        
        Returns:
            Tuple of (success, list of file paths)
        """
        if not self.tmsu_path:
            return False, []

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "untagged"],
            cwd=cwd,
        )

        if success and stdout:
            files = [Path(f.strip()) for f in stdout.splitlines() if f.strip()]
            logger.info(f"Found {len(files)} untagged files")
            return True, files

        return True, []

    def init_database(self, cwd: Path) -> tuple[bool, str]:
        """
        Initialize a new TMSU database in the given directory.
        
        Args:
            cwd: Directory to initialize database in
            
        Returns:
            Tuple of (success, error message if failed)
        """
        if not self.tmsu_path:
            return False, "TMSU not available"

        success, stdout, stderr = self._run_command(
            [self.tmsu_path, "init"],
            cwd=cwd,
        )

        if success:
            logger.info(f"Initialized TMSU database in {cwd}")
            return True, ""
        else:
            return False, stderr or "Unknown error"

    # -------------------------------------------------------------------------
    # ExifTool Operations
    # -------------------------------------------------------------------------

    def get_metadata(self, file_path: Path) -> ExifMetadata:
        """
        Get metadata for a file using exiftool.
        
        Args:
            file_path: Path to the file
            
        Returns:
            ExifMetadata object with parsed data or error
        """
        if not self.exiftool_path:
            return ExifMetadata(error="ExifTool not available")

        if not file_path.exists():
            return ExifMetadata(error="File not found")

        success, stdout, stderr = self._run_command(
            [self.exiftool_path, "-json", "-G", str(file_path)],
        )

        if success and stdout:
            try:
                data = json.loads(stdout)
                if data and isinstance(data, list):
                    logger.debug(f"Retrieved metadata for {file_path}")
                    return ExifMetadata(data=data[0])
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse exiftool JSON: {e}")
                return ExifMetadata(error=f"JSON parse error: {e}")

        return ExifMetadata(error=stderr or "No metadata available")


# =============================================================================
# Custom Widgets
# =============================================================================


class TagChip(Static):
    """
    A clickable tag chip widget with remove functionality.
    
    Displays a tag name with an 'x' button to remove it.
    """

    DEFAULT_CSS = """
    TagChip {
        height: 1;
        padding: 0 1;
        margin: 0 1 0 0;
        background: $primary-darken-2;
        color: $text;
    }
    
    TagChip:hover {
        background: $primary;
    }
    
    TagChip .tag-remove {
        color: $error;
    }
    """

    class RemoveRequested(Message):
        """Message sent when tag removal is requested."""

        def __init__(self, tag: str) -> None:
            self.tag = tag
            super().__init__()

    def __init__(self, tag: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tag = tag

    def compose(self) -> ComposeResult:
        yield Static(f"[bold]{self.tag}[/] ×", markup=True)

    def on_click(self) -> None:
        """Handle click to remove tag."""
        self.post_message(self.RemoveRequested(self.tag))


class TagEditor(Widget):
    """
    Widget for viewing and editing file tags.
    
    Displays current tags as chips and provides an input field
    for adding new tags.
    """

    DEFAULT_CSS = """
    TagEditor {
        height: auto;
        padding: 1;
    }
    
    TagEditor #tag-container {
        height: auto;
        layout: horizontal;
        overflow: hidden auto;
    }
    
    TagEditor #tag-input {
        margin-top: 1;
    }
    """

    class TagAdded(Message):
        """Message sent when a new tag is added."""

        def __init__(self, tag: str) -> None:
            self.tag = tag
            super().__init__()

    class TagRemoved(Message):
        """Message sent when a tag is removed."""

        def __init__(self, tag: str) -> None:
            self.tag = tag
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tags: list[str] = []

    def compose(self) -> ComposeResult:
        yield Label("Tags:", classes="section-label")
        yield Horizontal(id="tag-container")
        yield Input(placeholder="Add tag (press Enter)", id="tag-input")

    def set_tags(self, tags: list[str]) -> None:
        """Update displayed tags."""
        self._tags = tags
        container = self.query_one("#tag-container", Horizontal)
        container.remove_children()

        for tag in tags:
            container.mount(TagChip(tag))

    @on(Input.Submitted, "#tag-input")
    def on_tag_input(self, event: Input.Submitted) -> None:
        """Handle new tag submission."""
        tag = event.value.strip()
        if tag:
            self.post_message(self.TagAdded(tag))
            event.input.value = ""

    @on(TagChip.RemoveRequested)
    def on_tag_remove(self, event: TagChip.RemoveRequested) -> None:
        """Handle tag removal request."""
        self.post_message(self.TagRemoved(event.tag))


class MetadataPanel(Widget):
    """
    Widget for displaying file metadata from exiftool.
    
    Shows key-value pairs in a scrollable list.
    """

    DEFAULT_CSS = """
    MetadataPanel {
        height: 100%;
        padding: 1;
    }
    
    MetadataPanel #metadata-scroll {
        height: 1fr;
    }
    
    MetadataPanel .metadata-row {
        height: auto;
        margin-bottom: 0;
    }
    
    MetadataPanel .metadata-key {
        color: $text-muted;
        width: auto;
    }
    
    MetadataPanel .metadata-value {
        color: $text;
        width: 1fr;
    }
    """

    # Keys to display prominently
    PRIORITY_KEYS = [
        "File:FileName",
        "File:FileSize",
        "File:MIMEType",
        "File:FileModifyDate",
        "EXIF:ImageWidth",
        "EXIF:ImageHeight",
        "EXIF:Make",
        "EXIF:Model",
        "EXIF:DateTimeOriginal",
        "Composite:ImageSize",
        "Composite:Megapixels",
    ]

    def compose(self) -> ComposeResult:
        yield Label("Metadata:", classes="section-label")
        yield VerticalScroll(id="metadata-scroll")

    def set_metadata(self, metadata: ExifMetadata) -> None:
        """Update displayed metadata."""
        scroll = self.query_one("#metadata-scroll", VerticalScroll)
        scroll.remove_children()

        if metadata.error:
            scroll.mount(Static(f"[red]{metadata.error}[/]", markup=True))
            return

        if not metadata.data:
            scroll.mount(Static("[dim]No metadata available[/]", markup=True))
            return

        # Display priority keys first
        displayed = set()
        for key in self.PRIORITY_KEYS:
            if key in metadata.data:
                value = str(metadata.data[key])
                display_key = key.split(":")[-1]
                scroll.mount(
                    Static(
                        f"[cyan]{display_key}:[/] {value}",
                        markup=True,
                        classes="metadata-row",
                    )
                )
                displayed.add(key)

        # Then display remaining keys
        for key, value in sorted(metadata.data.items()):
            if key not in displayed and not key.startswith("SourceFile"):
                display_key = key.split(":")[-1] if ":" in key else key
                value_str = str(value)[:50]  # Truncate long values
                scroll.mount(
                    Static(
                        f"[dim]{display_key}:[/] {value_str}",
                        markup=True,
                        classes="metadata-row",
                    )
                )

    def clear(self) -> None:
        """Clear displayed metadata."""
        scroll = self.query_one("#metadata-scroll", VerticalScroll)
        scroll.remove_children()
        scroll.mount(Static("[dim]Select a file to view metadata[/]", markup=True))


class SourceTree(Tree[str]):
    """
    Navigation tree for file system and TMSU queries.
    
    Provides a combined view of:
    - File system directories
    - TMSU special queries (untagged, all tagged)
    """

    class DirectorySelected(Message):
        """Message sent when a directory is selected."""

        def __init__(self, path: Path) -> None:
            self.path = path
            super().__init__()

    class QuerySelected(Message):
        """Message sent when a TMSU query is selected."""

        def __init__(self, query: str) -> None:
            self.query = query
            super().__init__()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("Sources", **kwargs)
        self._expanded_paths: set[Path] = set()

    def on_mount(self) -> None:
        """Build the initial tree structure."""
        self.root.expand()

        # File System section
        fs_node = self.root.add("📁 File System", data="filesystem")
        fs_node.expand()

        # Add home directory
        home = Path.home()
        self._add_directory_node(fs_node, home)

        # Add current working directory if different
        cwd = Path.cwd()
        if cwd != home:
            self._add_directory_node(fs_node, cwd)

        # TMSU Queries section
        queries_node = self.root.add("🏷️ TMSU Queries", data="queries")
        queries_node.expand()
        queries_node.add_leaf("📋 All Tagged Files", data="query:all")
        queries_node.add_leaf("❓ Untagged Files", data="query:untagged")

    def _add_directory_node(
        self,
        parent: TreeNode[str],
        path: Path,
    ) -> TreeNode[str]:
        """Add a directory node to the tree."""
        label = f"📁 {path.name or str(path)}"
        node = parent.add(label, data=f"dir:{path}")
        node.allow_expand = True
        return node

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[str]) -> None:
        """Handle node expansion to lazily load directories."""
        node = event.node
        data = node.data

        if data and data.startswith("dir:"):
            path = Path(data[4:])
            if path not in self._expanded_paths:
                self._expanded_paths.add(path)
                self._populate_directory(node, path)

    def _populate_directory(self, node: TreeNode[str], path: Path) -> None:
        """Populate a directory node with its children."""
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

            for entry in entries:
                if entry.name.startswith("."):
                    continue  # Skip hidden files

                if entry.is_dir():
                    self._add_directory_node(node, entry)

        except PermissionError:
            node.add_leaf("⚠️ Permission denied", data="error")
        except Exception as e:
            logger.error(f"Error reading directory {path}: {e}")
            node.add_leaf(f"⚠️ Error: {e}", data="error")

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        """Handle node selection."""
        data = event.node.data

        if data and data.startswith("dir:"):
            path = Path(data[4:])
            self.post_message(self.DirectorySelected(path))
        elif data and data.startswith("query:"):
            query = data[6:]
            self.post_message(self.QuerySelected(query))


class TagList(ListView):
    """
    Scrollable list of all tags in the TMSU database.
    
    Clicking a tag filters the file list.
    """

    class TagFilterSelected(Message):
        """Message sent when a tag is selected for filtering."""

        def __init__(self, tag: str) -> None:
            self.tag = tag
            super().__init__()

    def set_tags(self, tags: list[str]) -> None:
        """Update the tag list."""
        self.clear()
        for tag in tags:
            self.append(ListItem(Label(f"🏷️ {tag}"), name=tag))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle tag selection."""
        if event.item.name:
            self.post_message(self.TagFilterSelected(event.item.name))


# =============================================================================
# Modal Screens
# =============================================================================


class ToolPathDialog(ModalScreen[dict[str, str]]):
    """
    Modal dialog for configuring tool paths.
    
    Displayed when tmsu or exiftool are not found in PATH.
    """

    DEFAULT_CSS = """
    ToolPathDialog {
        align: center middle;
    }
    
    ToolPathDialog #dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    
    ToolPathDialog .dialog-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $warning;
    }
    
    ToolPathDialog .dialog-text {
        margin-bottom: 1;
    }
    
    ToolPathDialog Input {
        margin-bottom: 1;
    }
    
    ToolPathDialog #buttons {
        margin-top: 1;
        align: center middle;
    }
    """

    def __init__(
        self,
        tmsu_missing: bool = False,
        exiftool_missing: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.tmsu_missing = tmsu_missing
        self.exiftool_missing = exiftool_missing

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("⚠️ External Tools Not Found", classes="dialog-title")
            yield Static(
                "Some external tools were not found in your PATH.\n"
                "Please provide the full path to each tool, or leave empty to skip.",
                classes="dialog-text",
            )

            if self.tmsu_missing:
                yield Label("TMSU Path:")
                yield Input(placeholder="/usr/bin/tmsu", id="tmsu-path")

            if self.exiftool_missing:
                yield Label("ExifTool Path:")
                yield Input(placeholder="/usr/bin/exiftool", id="exiftool-path")

            with Horizontal(id="buttons"):
                yield Button("Continue", variant="primary", id="continue")
                yield Button("Skip", variant="default", id="skip")

    @on(Button.Pressed, "#continue")
    def on_continue(self) -> None:
        """Handle continue button press."""
        result = {}

        if self.tmsu_missing:
            tmsu_input = self.query_one("#tmsu-path", Input)
            if tmsu_input.value.strip():
                result["tmsu"] = tmsu_input.value.strip()

        if self.exiftool_missing:
            exiftool_input = self.query_one("#exiftool-path", Input)
            if exiftool_input.value.strip():
                result["exiftool"] = exiftool_input.value.strip()

        self.dismiss(result)

    @on(Button.Pressed, "#skip")
    def on_skip(self) -> None:
        """Handle skip button press."""
        self.dismiss({})


class HelpScreen(ModalScreen[None]):
    """Help screen showing keyboard shortcuts and usage information."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("q", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    
    HelpScreen #help-container {
        width: 70;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    
    HelpScreen .help-title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
        color: $primary;
    }
    
    HelpScreen .help-section {
        margin-bottom: 1;
        color: $secondary;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-container"):
            yield Static("TMSU Explorer Help", classes="help-title")
            yield Static("Navigation:", classes="help-section")
            yield Static("  Tab / Shift+Tab  - Move between panes")
            yield Static("  ↑/↓              - Navigate lists/trees")
            yield Static("  Enter            - Select item")
            yield Static("")
            yield Static("Actions:", classes="help-section")
            yield Static("  F1 / ?           - Show this help")
            yield Static("  F5               - Refresh current view")
            yield Static("  Ctrl+Q           - Quit application")
            yield Static("  Ctrl+L           - Toggle log panel")
            yield Static("")
            yield Static("Tagging:", classes="help-section")
            yield Static("  Type in tag input and press Enter to add")
            yield Static("  Click tag chip × to remove tag")
            yield Static("")
            yield Static("Press Escape or Q to close", classes="help-section")


# =============================================================================
# Main Application
# =============================================================================


class TMSUExplorer(App[None]):
    """
    TMSU Explorer - A Zotero-style TUI for managing file tags.
    
    Features:
    - Three-pane interface: sources, files, inspector
    - TMSU integration for tagging
    - ExifTool integration for metadata
    - Crash-resistant design with graceful error handling
    """

    CSS_PATH = "tmsu_explorer.tcss"
    TITLE = "TMSU Explorer"
    SUB_TITLE = "Tag Manager"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("f1", "help", "Help"),
        Binding("question_mark", "help", "Help", show=False),
        Binding("f5", "refresh", "Refresh"),
        Binding("ctrl+l", "toggle_log", "Toggle Log"),
    ]

    # Reactive properties
    current_path: reactive[Optional[Path]] = reactive(None)
    selected_file: reactive[Optional[FileInfo]] = reactive(None)
    status_message: reactive[str] = reactive("Ready")

    def __init__(self) -> None:
        super().__init__()
        self.backend = Backend()
        self._all_tags: list[str] = []
        self._current_files: list[FileInfo] = []

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="main-container"):
            # Left sidebar
            with Vertical(id="left-sidebar"):
                yield SourceTree(id="source-tree")
                yield Label("All Tags", id="tags-label")
                yield TagList(id="tag-list")

            # Middle pane - file list
            with Vertical(id="middle-pane"):
                yield Label("Files", id="files-label")
                yield DataTable(id="file-table")

            # Right pane - inspector
            with Vertical(id="right-pane"):
                yield Label("Inspector", id="inspector-label")
                yield MetadataPanel(id="metadata-panel")
                yield TagEditor(id="tag-editor")

        # Status bar and log
        yield Static(id="status-bar")
        yield RichLog(id="log-panel", highlight=True, markup=True)

        yield Footer()

    def on_mount(self) -> None:
        """Initialize the application on mount."""
        logger.info("Application mounted")

        # Setup file table
        table = self.query_one("#file-table", DataTable)
        table.add_columns("Name", "Path", "Size", "Modified")
        table.cursor_type = "row"

        # Check for missing tools
        self._check_tools()

        # Load initial tags
        self._refresh_tags()

        # Set initial status
        self._update_status("Ready - Select a directory or query to browse files")

    def _check_tools(self) -> None:
        """Check if external tools are available and prompt if not."""
        tmsu_missing = not self.backend.tmsu_available
        exiftool_missing = not self.backend.exiftool_available

        if tmsu_missing or exiftool_missing:
            self.push_screen(
                ToolPathDialog(
                    tmsu_missing=tmsu_missing,
                    exiftool_missing=exiftool_missing,
                ),
                self._handle_tool_paths,
            )

    def _handle_tool_paths(self, result: dict[str, str]) -> None:
        """Handle result from tool path dialog."""
        if "tmsu" in result:
            if self.backend.set_tmsu_path(result["tmsu"]):
                self._log("✓ TMSU path configured")
            else:
                self._log("[red]✗ Invalid TMSU path[/]")

        if "exiftool" in result:
            if self.backend.set_exiftool_path(result["exiftool"]):
                self._log("✓ ExifTool path configured")
            else:
                self._log("[red]✗ Invalid ExifTool path[/]")

        self._refresh_tags()

    def _update_status(self, message: str) -> None:
        """Update the status bar."""
        self.status_message = message
        status_bar = self.query_one("#status-bar", Static)
        status_bar.update(f" {message}")

    def _log(self, message: str) -> None:
        """Log a message to the log panel."""
        log_panel = self.query_one("#log-panel", RichLog)
        log_panel.write(message)

    def _refresh_tags(self) -> None:
        """Refresh the tag list from TMSU."""
        success, tags = self.backend.get_all_tags(self.current_path)
        self._all_tags = tags

        tag_list = self.query_one("#tag-list", TagList)
        tag_list.set_tags(tags)

        if success:
            self._log(f"Loaded {len(tags)} tags")
        else:
            self._log("[yellow]Could not load tags (no TMSU database?)[/]")

    def _load_directory(self, path: Path) -> None:
        """Load files from a directory into the file table."""
        self.current_path = path
        self._current_files.clear()

        try:
            for entry in sorted(path.iterdir(), key=lambda p: p.name.lower()):
                if not entry.name.startswith(".") and entry.is_file():
                    self._current_files.append(FileInfo.from_path(entry))
        except PermissionError:
            self._log(f"[red]Permission denied: {path}[/]")
            self._update_status(f"Permission denied: {path}")
            return
        except Exception as e:
            logger.exception(f"Error reading directory: {e}")
            self._log(f"[red]Error reading directory: {e}[/]")
            return

        self._populate_file_table()
        self._update_status(f"Showing {len(self._current_files)} files from {path}")

        # Refresh tags for this directory
        self._refresh_tags()

    def _load_query_results(self, query: str) -> None:
        """Load files from a TMSU query."""
        self._current_files.clear()

        if query == "all":
            # Get all tagged files
            success, tags = self.backend.get_all_tags(self.current_path)
            all_files: set[Path] = set()

            for tag in tags:
                _, files = self.backend.query_files(tag, self.current_path)
                all_files.update(files)

            for path in sorted(all_files):
                if path.exists():
                    self._current_files.append(FileInfo.from_path(path))

        elif query == "untagged":
            success, files = self.backend.get_untagged_files(self.current_path)
            for path in files:
                if path.exists():
                    self._current_files.append(FileInfo.from_path(path))

        else:
            # Tag query
            success, files = self.backend.query_files(query, self.current_path)
            for path in files:
                if path.exists():
                    self._current_files.append(FileInfo.from_path(path))

        self._populate_file_table()
        self._update_status(f"Query '{query}': {len(self._current_files)} files")

    def _populate_file_table(self) -> None:
        """Populate the file table with current files."""
        table = self.query_one("#file-table", DataTable)
        table.clear()

        for file_info in self._current_files:
            size_str = self._format_size(file_info.size)
            modified_str = (
                file_info.modified.strftime("%Y-%m-%d %H:%M")
                if file_info.modified
                else "-"
            )
            table.add_row(
                file_info.name,
                str(file_info.path.parent),
                size_str,
                modified_str,
                key=str(file_info.path),
            )

    @staticmethod
    def _format_size(size: int) -> str:
        """Format file size in human-readable format."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @work(exclusive=True)
    async def _load_file_metadata(self, file_info: FileInfo) -> None:
        """Load metadata for the selected file (async worker)."""
        metadata_panel = self.query_one("#metadata-panel", MetadataPanel)
        tag_editor = self.query_one("#tag-editor", TagEditor)

        # Get metadata
        metadata = self.backend.get_metadata(file_info.path)
        metadata_panel.set_metadata(metadata)

        # Get tags
        success, tags = self.backend.get_file_tags(
            file_info.path,
            self.current_path,
        )
        tag_editor.set_tags(tags)

    # -------------------------------------------------------------------------
    # Event Handlers
    # -------------------------------------------------------------------------

    @on(SourceTree.DirectorySelected)
    def on_directory_selected(self, event: SourceTree.DirectorySelected) -> None:
        """Handle directory selection in source tree."""
        self._load_directory(event.path)

    @on(SourceTree.QuerySelected)
    def on_query_selected(self, event: SourceTree.QuerySelected) -> None:
        """Handle TMSU query selection."""
        self._load_query_results(event.query)

    @on(TagList.TagFilterSelected)
    def on_tag_filter(self, event: TagList.TagFilterSelected) -> None:
        """Handle tag selection for filtering."""
        self._load_query_results(event.tag)

    @on(DataTable.RowHighlighted)
    def on_file_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Handle file selection in the file table."""
        if event.row_key and event.row_key.value:
            path = Path(event.row_key.value)
            for file_info in self._current_files:
                if file_info.path == path:
                    self.selected_file = file_info
                    self._load_file_metadata(file_info)
                    break

    @on(TagEditor.TagAdded)
    def on_tag_added(self, event: TagEditor.TagAdded) -> None:
        """Handle adding a tag to the selected file."""
        if not self.selected_file:
            self._log("[yellow]No file selected[/]")
            return

        success, error = self.backend.add_tag(
            self.selected_file.path,
            event.tag,
            self.current_path,
        )

        if success:
            self._log(f"Added tag '{event.tag}' to {self.selected_file.name}")
            self._load_file_metadata(self.selected_file)
            self._refresh_tags()
        else:
            self._log(f"[red]Failed to add tag: {error}[/]")

    @on(TagEditor.TagRemoved)
    def on_tag_removed(self, event: TagEditor.TagRemoved) -> None:
        """Handle removing a tag from the selected file."""
        if not self.selected_file:
            return

        success, error = self.backend.remove_tag(
            self.selected_file.path,
            event.tag,
            self.current_path,
        )

        if success:
            self._log(f"Removed tag '{event.tag}' from {self.selected_file.name}")
            self._load_file_metadata(self.selected_file)
            self._refresh_tags()
        else:
            self._log(f"[red]Failed to remove tag: {error}[/]")

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_help(self) -> None:
        """Show help screen."""
        self.push_screen(HelpScreen())

    def action_refresh(self) -> None:
        """Refresh the current view."""
        if self.current_path:
            self._load_directory(self.current_path)
        self._refresh_tags()
        self._log("Refreshed view")

    def action_toggle_log(self) -> None:
        """Toggle the log panel visibility."""
        log_panel = self.query_one("#log-panel", RichLog)
        log_panel.toggle_class("hidden")


# =============================================================================
# Entry Point
# =============================================================================


def main() -> None:
    """Application entry point."""
    try:
        app = TMSUExplorer()
        app.run()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        print(f"Fatal error: {e}")
        print(f"Check {LOG_FILE} for details")
        raise


if __name__ == "__main__":
    main()
