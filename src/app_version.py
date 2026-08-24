"""Immutable application identity used by the GUI and packaging script."""

APP_VERSION = "v0.7.1"
APP_AUTHOR = "Chengz2Z"
APP_FREE_AUTHOR = "Chengz2Z"


def branded_window_title(product_title: str) -> str:
    """Build the licensed-edition title with compiled-in branding."""
    return f"{product_title.strip()} {APP_VERSION} by {APP_AUTHOR}"


def branded_free_window_title(product_title: str) -> str:
    """Build the free-edition title with compiled-in release branding."""
    return f"{product_title.strip()} {APP_VERSION} Release by {APP_FREE_AUTHOR}"
