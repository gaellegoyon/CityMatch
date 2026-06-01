"""Sérialisation sûre des objets numpy/pandas pour LangGraph/JSON."""

def to_python(obj):
    try:
        import numpy as np
    except Exception:  # pragma: no cover
        np = None
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_python(v) for v in obj]
    if np is not None:
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
    return obj
