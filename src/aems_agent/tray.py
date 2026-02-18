"""
System tray integration for the AEMS Local Bridge Agent.

Provides a tray icon with:
- Status indicator (green = running, yellow = no storage path)
- Menu: Open Settings, Set Storage Folder, Show Token, Quit

Requires: pystray, pillow (PIL)
"""

import logging
import threading
import webbrowser
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _create_icon_image(color: str = "green") -> "PIL.Image.Image":
    """Create a simple colored circle icon."""
    from PIL import Image, ImageDraw

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    color_map = {
        "green": (76, 175, 80, 255),
        "yellow": (255, 193, 7, 255),
        "red": (244, 67, 54, 255),
    }
    fill = color_map.get(color, color_map["green"])

    # Draw circle with border
    draw.ellipse([4, 4, size - 4, size - 4], fill=fill, outline=(255, 255, 255, 200), width=2)

    # Draw "A" letter in the center
    try:
        from PIL import ImageFont

        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "A", font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) // 2
        y = (size - text_h) // 2
        draw.text((x, y), "A", fill=(255, 255, 255, 255), font=font)
    except Exception:
        pass

    return img


def _open_folder_picker(config_dir: Path) -> None:
    """Open a native folder picker dialog to set the storage path."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        folder = filedialog.askdirectory(
            title="Select AEMS Exam Storage Folder",
            mustexist=False,
        )

        root.destroy()

        if folder:
            from .config import load_config, save_config

            config = load_config(config_dir)
            config.storage_path = str(Path(folder).resolve())
            save_config(config, config_dir)
            logger.info("Storage path set via tray: %s", folder)
    except Exception as e:
        logger.error("Folder picker failed: %s", e)


def create_tray(config_dir: Path) -> "pystray.Icon":
    """
    Create and return a pystray Icon instance.

    Args:
        config_dir: Agent config directory.

    Returns:
        Configured pystray Icon (call .run() to start).
    """
    import pystray  # type: ignore

    from .config import get_auth_token, load_config

    config = load_config(config_dir)
    icon_color = "green" if config.storage_path else "yellow"
    image = _create_icon_image(icon_color)

    def on_open_settings(icon: "pystray.Icon", item: "pystray.MenuItem") -> None:
        cfg = load_config(config_dir)
        url = f"http://{cfg.host}:{cfg.port}/status"
        # Open the AEMS web settings page instead
        webbrowser.open("http://127.0.0.1:8080/settings#privacy")

    def on_set_folder(icon: "pystray.Icon", item: "pystray.MenuItem") -> None:
        _open_folder_picker(config_dir)
        # Update icon color based on new config
        cfg = load_config(config_dir)
        new_color = "green" if cfg.storage_path else "yellow"
        icon.icon = _create_icon_image(new_color)

    def on_show_token(icon: "pystray.Icon", item: "pystray.MenuItem") -> None:
        token = get_auth_token(config_dir)
        if token:
            # Copy to clipboard if possible
            try:
                import tkinter as tk

                root = tk.Tk()
                root.withdraw()
                root.clipboard_clear()
                root.clipboard_append(token)
                root.update()
                root.destroy()
                logger.info("Token copied to clipboard")
            except Exception:
                logger.info("Auth token: %s", token)

    def on_quit(icon: "pystray.Icon", item: "pystray.MenuItem") -> None:
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Open Settings", on_open_settings, default=True),
        pystray.MenuItem("Set Storage Folder", on_set_folder),
        pystray.MenuItem("Copy Token", on_show_token),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    icon = pystray.Icon(
        name="aems-agent",
        icon=image,
        title="AEMS Local Bridge Agent",
        menu=menu,
    )

    return icon


def start_tray_thread(config_dir: Path) -> threading.Thread:
    """
    Start the system tray in a background daemon thread.

    Args:
        config_dir: Agent config directory.

    Returns:
        The thread running the tray icon.
    """
    icon = create_tray(config_dir)

    thread = threading.Thread(target=icon.run, daemon=True, name="aems-tray")
    thread.start()

    return thread
