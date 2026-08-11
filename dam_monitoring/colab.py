"""Google Colab detection, isolated so importing the package elsewhere never
depends on `google.colab` being installed."""

from typing import Any

colab_files: Any = None
IS_COLAB = False

try:
    from google.colab import files as _colab_files  # type: ignore

    colab_files = _colab_files
    IS_COLAB = True
except ImportError:
    pass
