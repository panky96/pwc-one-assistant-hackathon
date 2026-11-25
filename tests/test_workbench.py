from streamlit.testing.v1 import AppTest


def test_workbench_renders_all_tabs_without_exception():
    at = AppTest.from_file(str(__import__("pathlib").Path(__file__).parent.parent / "app.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 6
