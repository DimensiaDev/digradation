"""Smoke test: package imports cleanly."""


def test_package_imports():
    import edu_rag

    assert edu_rag.__name__ == "edu_rag"
