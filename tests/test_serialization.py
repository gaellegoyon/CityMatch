import math
from datetime import date, datetime
from decimal import Decimal

from utils.serialization import to_python


def test_to_python_keeps_basic_types() -> None:
    assert to_python(None) is None
    assert to_python("abc") == "abc"
    assert to_python(True) is True
    assert to_python(12) == 12


def test_to_python_converts_nested_structures() -> None:
    data = {
        "a": (1, 2),
        "b": {"x", "y"},
        3: {"nested": Decimal("12.5")},
    }

    result = to_python(data)

    assert result["a"] == [1, 2]
    assert sorted(result["b"]) == ["x", "y"]
    assert result["3"]["nested"] == 12.5


def test_to_python_converts_dates() -> None:
    assert to_python(date(2026, 1, 2)) == "2026-01-02"
    assert to_python(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05"


def test_to_python_converts_nan_and_infinity_to_none() -> None:
    assert to_python(float("nan")) is None
    assert to_python(float("inf")) is None
    assert to_python(-float("inf")) is None


def test_to_python_converts_numpy_values_when_available() -> None:
    pytest_np = __import__("numpy")

    assert to_python(pytest_np.int64(3)) == 3
    assert to_python(pytest_np.float64(3.5)) == 3.5
    assert to_python(pytest_np.float64(math.nan)) is None
    assert to_python(pytest_np.array([1, 2, 3])) == [1, 2, 3]


def test_to_python_converts_pandas_values_when_available() -> None:
    pytest_pd = __import__("pandas")

    df = pytest_pd.DataFrame([{"score": pytest_pd.NA}, {"score": 10}])

    assert to_python(pytest_pd.NA) is None
    assert to_python(pytest_pd.Timestamp("2026-01-02")) == "2026-01-02T00:00:00"
    assert to_python(df) == [{"score": None}, {"score": 10}]