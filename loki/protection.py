"""Mandatory WoW protection plus user additions. Battle.net is not a global block."""

from fnmatch import fnmatchcase

from .identity import normalized_path

# Multiple independent identifiers cover retail/classic/test clients and renamed images
# whose normal version metadata still identifies World of Warcraft.
WOW_EXECUTABLES = (
    "wow.exe",
    "wow-64.exe",
    "wowclassic*.exe",
    "wowb.exe",
    "wowt.exe",
    "wowbeta*.exe",
)
WOW_TITLES = ("*world of warcraft*",)
WOW_CLASSES = ("gxwindowclass*",)
WOW_PRODUCTS = ("*world of warcraft*",)
WOW_PATH_PARTS = (
    "world of warcraft",
    "_classic_",
    "_classic_era_",
    "_classic_beta_",
    "_retail_",
    "_ptr_",
)


def matches(value, patterns):
    return any(fnmatchcase(value.casefold(), pattern.casefold()) for pattern in patterns)


def protected_reason(identity, extra_executables=(), extra_titles=(), extra_paths=()):
    if identity is None:
        return None  # Unknown metadata is rejected separately by the fail-closed guard.
    if matches(identity.executable_name, WOW_EXECUTABLES + tuple(extra_executables)):
        return "protected executable " + identity.executable_name
    if matches(identity.title, WOW_TITLES + tuple(extra_titles)):
        return "protected window title " + identity.title
    if matches(identity.class_name, WOW_CLASSES):
        return "protected WoW window class " + identity.class_name
    path = normalized_path(identity.executable_path)
    components = path.replace("/", "\\").split("\\")
    if any(part in WOW_PATH_PARTS for part in components) or matches(path, extra_paths):
        return "protected executable path " + identity.executable_path
    if matches(identity.original_filename, WOW_EXECUTABLES) or any(
        matches(value, WOW_PRODUCTS) for value in (identity.product_name, identity.file_description)
    ):
        return "protected World of Warcraft version metadata"
    return None


def profile_is_protected(profile):
    if not isinstance(profile.name, str):
        return True
    if (
        profile.monitor_only
        or "world of warcraft" in profile.name.casefold()
        or profile.name.casefold().startswith("wow")
    ):
        return True
    return profile.target is not None and protected_reason(profile.target.window) is not None
