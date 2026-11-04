import pytest
from app.schemas import Params
from pydantic import ValidationError


def test_params_bounds() -> None:
    assert Params(conf=0.4, iou=0.5).conf == 0.4
    with pytest.raises(ValidationError):
        Params(conf=1.1, iou=0.5)
