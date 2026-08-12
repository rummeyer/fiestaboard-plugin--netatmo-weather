"""Guards that keep manifest, code, docs and changelog from drifting apart.

The manifest is not just metadata — FiestaBoard renders the plugin directory
from it and the template editor offers whatever variables it declares. A field
that no longer matches the code is a broken listing, not a cosmetic slip.
"""

import json
import pathlib
import re

import pytest
from src.devices import BoardContext

from plugins.netatmo_weather import DEFAULT_SECONDARY, DEFAULT_UNITS, SECONDARY_CHOICES, UNITS, NetatmoWeatherPlugin

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOARD_SHAPES = {"flagship": (6, 22), "note": (3, 15)}
COLOR_MARKER = re.compile(r"\{(?:6[3-9]|7[01])\}")

# The settings the committed previews were rendered with. Changing a preview
# means changing these too — that is the point.
PREVIEW_MODULES = None  # the default: every module the station has


@pytest.fixture(scope="module")
def manifest():
    return json.loads((ROOT / "manifest.json").read_text())


def count_tiles(row: str) -> int:
    """Flaps used by a row. A color marker like ``{66}`` is one tile, not four."""
    return len(COLOR_MARKER.sub("X", row))


class TestIdentity:
    def test_id_matches_the_plugin_class(self, manifest):
        assert manifest["id"] == NetatmoWeatherPlugin(manifest).plugin_id

    def test_version_is_semver(self, manifest):
        assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])

    def test_version_matches_the_changelog(self, manifest):
        """The changelog is the source of truth; a release without an entry is a lie."""
        changelog = (ROOT / "CHANGELOG.md").read_text()
        released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        assert released, "CHANGELOG.md has no released versions"
        assert manifest["version"] == released[0], (
            f"manifest says {manifest['version']}, newest changelog entry is {released[0]}"
        )

    def test_changelog_versions_descend(self):
        changelog = (ROOT / "CHANGELOG.md").read_text()
        released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        keyed = [tuple(int(part) for part in v.split(".")) for v in released]
        assert keyed == sorted(keyed, reverse=True)


class TestSettingsSchema:
    def test_unit_options_match_the_code(self, manifest):
        assert tuple(manifest["settings_schema"]["properties"]["units"]["enum"]) == UNITS

    def test_second_reading_options_match_the_code(self, manifest):
        assert tuple(manifest["settings_schema"]["properties"]["secondary"]["enum"]) == SECONDARY_CHOICES

    def test_schema_defaults_match_the_code_fallbacks(self, manifest):
        """A board configured purely from defaults must render what the form shows."""
        properties = manifest["settings_schema"]["properties"]
        assert properties["units"]["default"] == DEFAULT_UNITS
        assert properties["secondary"]["default"] == DEFAULT_SECONDARY

    def test_every_enum_has_a_label(self, manifest):
        for name, prop in manifest["settings_schema"]["properties"].items():
            if "enum" in prop:
                assert len(prop.get("enumNames", [])) == len(prop["enum"]), f"{name} labels do not line up"

    def test_every_enum_default_is_one_of_its_options(self, manifest):
        for name, prop in manifest["settings_schema"]["properties"].items():
            if "enum" in prop and "default" in prop:
                assert prop["default"] in prop["enum"], f"{name} default is not an option"

    def test_credentials_are_masked_in_the_settings_form(self, manifest):
        properties = manifest["settings_schema"]["properties"]
        for name in ("client_id", "client_secret", "refresh_token"):
            assert properties[name].get("ui:widget") == "password", f"{name} is not masked"

    def test_the_refresh_floor_is_at_least_the_stations_own_interval(self, manifest):
        """Netatmo measures every five minutes; polling faster only spends quota."""
        assert manifest["min_refresh_seconds"] >= 300
        assert manifest["settings_schema"]["properties"]["refresh_seconds"]["minimum"] >= 300

    def test_the_module_picker_names_a_labels_field_that_exists(self, manifest):
        properties = manifest["settings_schema"]["properties"]
        options = properties["modules"]["ui:options"]
        assert options["multiple"] is True, "labels_field requires a multi-select"
        assert options["labels_field"] in properties


class TestVariables:
    def test_declared_variables_are_the_ones_the_plugin_produces(self, manifest, make_plugin):
        """A declared variable that never materialises renders as blank on the board."""
        produced = set(make_plugin().get_data(BoardContext.from_device_type("flagship")).data)
        declared = set(manifest["variables"]["simple"]) | set(manifest["variables"]["arrays"])
        assert produced == declared

    def test_the_module_array_declares_the_fields_it_produces(self, manifest, make_plugin):
        modules = make_plugin().get_data(BoardContext.from_device_type("flagship")).data["modules"]
        assert set(modules[0]) == set(manifest["variables"]["arrays"]["modules"]["item_fields"])

    def test_the_array_label_field_is_one_of_its_own_fields(self, manifest):
        array = manifest["variables"]["arrays"]["modules"]
        assert array["label_field"] in array["item_fields"]

    def test_every_variable_names_a_group_that_exists(self, manifest):
        groups = set(manifest["variables"]["groups"])
        for name, spec in manifest["variables"]["simple"].items():
            assert spec["group"] in groups, f"{name} points at an unknown group"

    def test_max_lengths_agree_with_the_variable_declarations(self, manifest):
        for name, declared in manifest["max_lengths"].items():
            if name in manifest["variables"]["simple"]:
                assert manifest["variables"]["simple"][name]["max_length"] == declared

    def test_color_rules_name_variables_the_plugin_produces(self, manifest):
        assert set(manifest["color_rules_schema"]) <= set(manifest["variables"]["simple"])

    def test_color_rules_never_name_an_array_field(self, manifest):
        """FiestaBoard's colour engine reads only the first two segments of an
        expression, so a rule on an array item would silently never fire.
        """
        arrays = set(manifest["variables"]["arrays"])
        assert not (set(manifest["color_rules_schema"]) & arrays)
        for field, spec in manifest["color_rules_schema"].items():
            for rule in spec.get("default_rules", []):
                assert {"condition", "value", "color"} <= set(rule), f"{field} has an incomplete rule"


class TestBoardPreviews:
    def test_teaser_fits_the_narrowest_board(self, manifest):
        assert count_tiles(manifest["teaser"]) <= 15

    def test_preview_rows_fit_their_board(self, manifest):
        for preview in manifest["previews"]:
            rows, cols = BOARD_SHAPES[preview["device_type"]]
            assert len(preview["rows"]) <= rows
            for row in preview["rows"]:
                assert count_tiles(row) <= cols, f"{preview['device_type']} row is too wide"

    def test_both_board_shapes_are_covered(self, manifest):
        assert {p["device_type"] for p in manifest["previews"]} == set(BOARD_SHAPES)

    @pytest.mark.parametrize("device_type", sorted(BOARD_SHAPES))
    def test_previews_match_what_the_plugin_actually_renders(self, manifest, device_type, make_plugin):
        """A hand-edited preview that no longer matches the code misleads the directory."""
        plugin = make_plugin(modules=PREVIEW_MODULES)
        rendered = plugin.get_data(BoardContext.from_device_type(device_type)).formatted_lines
        declared = next(p["rows"] for p in manifest["previews"] if p["device_type"] == device_type)
        assert declared == rendered


class TestDocumentation:
    def test_the_env_vars_the_code_reads_are_declared(self, manifest):
        source = (ROOT / "__init__.py").read_text()
        declared = {entry["name"] for entry in manifest["env_vars"]}
        assert set(re.findall(r'"(NETATMO_[A-Z_]+)"', source)) == declared

    def test_no_screenshot_entry_points_at_a_missing_file(self, manifest):
        for shot in manifest.get("screenshots", []):
            assert (ROOT / shot["src"]).is_file(), f"missing {shot['src']}"

    @pytest.mark.parametrize("document", ["README.md", "docs/SETUP.md"])
    def test_docs_link_only_to_images_that_exist(self, document):
        text = (ROOT / document).read_text()
        for target in re.findall(r"!\[[^\]]*\]\(\./([^)]+)\)", text):
            source = ROOT / ("docs" if document.startswith("docs/") else "") / target
            resolved = source if source.is_file() else ROOT / target
            assert resolved.is_file(), f"{document} references missing image {target}"

    @pytest.mark.parametrize("document", ["README.md", "docs/SETUP.md"])
    def test_the_docs_carry_no_real_looking_credentials(self, document):
        """A copied-in token is the one mistake that cannot be undone by an edit."""
        text = (ROOT / document).read_text()
        assert not re.search(r"\b[0-9a-f]{24}\b", text), f"{document} looks like it contains a real client id"
