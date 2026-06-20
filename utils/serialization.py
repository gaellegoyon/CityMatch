"""
Sérialisation sûre des objets Python, NumPy et pandas pour LangGraph, JSON
et les colonnes JSON SQLAlchemy.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any

try:
    import numpy as np
except ModuleNotFoundError:  # pragma: no cover
    np = None

try:
    import pandas as pd
except ModuleNotFoundError:  # pragma: no cover
    pd = None


def to_python(obj: Any) -> Any:
    """Convertit récursivement un objet en structure compatible JSON."""
    if obj is None or isinstance(obj, str | bool | int):
        return obj

    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, datetime | date):
        return obj.isoformat()

    if isinstance(obj, dict):
        return {str(key): to_python(value) for key, value in obj.items()}

    if isinstance(obj, list | tuple | set):
        return [to_python(value) for value in obj]

    if np is not None:
        if isinstance(obj, np.bool_):
            return bool(obj)

        if isinstance(obj, np.integer):
            return int(obj)

        if isinstance(obj, np.floating):
            value = float(obj)
            return value if math.isfinite(value) else None

        if isinstance(obj, np.ndarray):
            return to_python(obj.tolist())

    if pd is not None:
        if obj is pd.NA or obj is pd.NaT:
            return None

        if isinstance(obj, pd.Timestamp):
            return obj.isoformat()

        if isinstance(obj, pd.Timedelta):
            return str(obj)

        if isinstance(obj, pd.Series):
            return to_python(obj.to_dict())

        if isinstance(obj, pd.DataFrame):
            return to_python(obj.to_dict(orient="records"))

        try:
            if pd.isna(obj):
                return None
        except (TypeError, ValueError):
            pass

    return obj