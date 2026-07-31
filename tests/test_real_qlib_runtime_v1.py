from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from research_lab.research.real_qlib_runtime_v1 import (
    QlibPreparationParityError,
    QlibRuntimeUnavailable,
    build_real_qlib_preparation_parity_v1,
    build_real_qlib_runtime_metadata_v1,
    prepare_real_qlib_segments_v1,
)


SEGMENTS = {
    "discovery": ("2020-01-01", "2020-01-06"),
    "development": ("2020-01-07", "2020-01-10"),
}


def _frame() -> pd.DataFrame:
    index = pd.MultiIndex.from_product(
        [pd.bdate_range("2020-01-01", periods=8), ["AAA", "BBB"]],
        names=["datetime", "instrument"],
    )
    return pd.DataFrame(
        {
            "MOM_6_1": pd.Series(range(len(index)), index=index, dtype="float64"),
            "forward_return_5d": 0.01,
        },
        index=index,
    )


def _direct_segments(frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    dates = frame.index.get_level_values("datetime")
    return {
        name: frame.loc[(dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))].copy()
        for name, (start, end) in SEGMENTS.items()
    }


def test_unavailable_runtime_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)

    metadata = build_real_qlib_runtime_metadata_v1()

    assert metadata["status"] == "QLIB_RUNTIME_UNAVAILABLE"
    assert metadata["is_real_qlib"] is False
    with pytest.raises(QlibRuntimeUnavailable, match="QLIB_RUNTIME_UNAVAILABLE"):
        prepare_real_qlib_segments_v1(
            _frame(),
            feature_columns=("MOM_6_1",),
            label_column="forward_return_5d",
            segments=SEGMENTS,
        )


def test_injected_runtime_must_identify_itself_as_real_qlib() -> None:
    class FakeRuntime:
        is_real_qlib = False

    with pytest.raises(ValueError, match="real Qlib"):
        prepare_real_qlib_segments_v1(
            _frame(),
            feature_columns=("MOM_6_1",),
            label_column="forward_return_5d",
            segments=SEGMENTS,
            runtime=FakeRuntime(),
        )


def test_injected_real_runtime_prepares_closed_segments() -> None:
    class FakeRuntime:
        is_real_qlib = True

        @staticmethod
        def prepare_segments(
            frame: pd.DataFrame,
            *,
            feature_columns: tuple[str, ...],
            label_column: str,
            segments: dict[str, tuple[str, str]],
        ) -> dict[str, pd.DataFrame]:
            assert feature_columns == ("MOM_6_1",)
            assert label_column == "forward_return_5d"
            assert segments == SEGMENTS
            return _direct_segments(frame)

    result = prepare_real_qlib_segments_v1(
        _frame(),
        feature_columns=("MOM_6_1",),
        label_column="forward_return_5d",
        segments=SEGMENTS,
        runtime=FakeRuntime(),
    )

    assert set(result) == set(SEGMENTS)
    assert result["discovery"].index.names == ["datetime", "instrument"]
    assert result["development"].index.get_level_values("datetime").max() <= pd.Timestamp("2020-01-10")


def test_runtime_metadata_is_deterministic_and_path_free(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    monkeypatch.setenv("QLIB_TEST_SECRET", "do-not-leak")

    first = build_real_qlib_runtime_metadata_v1()
    second = build_real_qlib_runtime_metadata_v1()
    encoded = json.dumps(first, sort_keys=True, separators=(",", ":"))

    assert first == second
    assert set(first) == {
        "version",
        "status",
        "is_real_qlib",
        "qlib_version",
        "python_version",
        "runtime_sha256",
    }
    assert len(first["runtime_sha256"]) == 64
    assert str(tmp_path) not in encoded
    assert "do-not-leak" not in encoded


def test_preparation_parity_passes_only_for_exact_segments() -> None:
    source = _frame()
    prepared = _direct_segments(source)

    result = build_real_qlib_preparation_parity_v1(
        source,
        prepared,
        feature_columns=("MOM_6_1",),
        label_column="forward_return_5d",
        segments=SEGMENTS,
    )

    assert result["status"] == "PASS"
    assert result["segment_row_counts"] == {"development": 8, "discovery": 8}
    assert result["source_frame_sha256"] == result["prepared_frame_sha256"]
    assert len(result["canonical_parity_sha256"]) == 64


@pytest.mark.parametrize("corruption", ["missing", "reordered", "changed"])
def test_preparation_parity_rejects_any_difference(corruption: str) -> None:
    source = _frame()
    prepared = _direct_segments(source)
    development = prepared["development"]
    if corruption == "missing":
        prepared["development"] = development.iloc[1:]
    elif corruption == "reordered":
        prepared["development"] = development.iloc[::-1]
    else:
        prepared["development"] = development.copy()
        prepared["development"].iloc[0, 1] += 1e-12

    with pytest.raises(QlibPreparationParityError, match="QLIB_PREPARATION_PARITY_FAILED"):
        build_real_qlib_preparation_parity_v1(
            source,
            prepared,
            feature_columns=("MOM_6_1",),
            label_column="forward_return_5d",
            segments=SEGMENTS,
        )
