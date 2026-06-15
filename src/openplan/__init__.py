from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("openplan")
except PackageNotFoundError:
    __version__ = "0.8.1"

VERSION = __version__
