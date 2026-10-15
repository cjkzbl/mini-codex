import pytest


@pytest.fixture(autouse=True)
def system_fonts(request):
    if "qapp" in request.fixturenames:
        from minicodex.frontend.ui.fonts import configure_fonts

        configure_fonts(request.getfixturevalue("qapp"))
