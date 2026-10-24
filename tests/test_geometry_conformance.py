"""Board-geometry conformance for the Netatmo plugin.

Runs the shared FiestaBoard conformance suite (``src/plugins/
geometry_conformance.py`` in core, importable because core is on
``PYTHONPATH`` in plugin CI) across every board shape the platform supports,
from a Note to the largest FiestaPanel grid. It renders the plugin many times
and never touches the network: ``station_body`` returns the fixture payload.
"""

from src.plugins.geometry_conformance import assert_board_conformance

from .conftest import MANIFEST


def test_renders_on_every_board_shape(make_plugin):
    assert_board_conformance(lambda: make_plugin(), manifest=MANIFEST, require_note_array_preview=True)
