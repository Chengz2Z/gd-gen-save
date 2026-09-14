"""Immutable application identity used by the GUI and packaging script."""

APP_VERSION = "v1.0"
APP_AUTHOR = "Chengz2Z"


def branded_window_title(product_title: str) -> str:
    """Build the application title with compiled-in branding."""
    return f"{product_title.strip()} {APP_VERSION} by {APP_AUTHOR}"
