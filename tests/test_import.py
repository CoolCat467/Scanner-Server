import sanescansrv


def test_has_run() -> None:
    assert hasattr(sanescansrv, "run")
    assert callable(sanescansrv.run)
