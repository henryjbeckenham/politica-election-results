import unittest

import yaml

from politica_erd.build import PROJECT_ROOT


class Stage1341MapUsabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = yaml.safe_load(
            (PROJECT_ROOT / "config" / "visualisation_contract.yml").read_text(
                encoding="utf-8"
            )
        )
        cls.map_source = (
            PROJECT_ROOT / "visualisation/src/foundation/map.js"
        ).read_text(encoding="utf-8")
        cls.results_source = (
            PROJECT_ROOT / "visualisation/src/components/results.js"
        ).read_text(encoding="utf-8")
        cls.css = (PROJECT_ROOT / "visualisation/src/politica.css").read_text(
            encoding="utf-8"
        )

    def test_contract_governs_practical_deep_zoom_and_focus(self):
        self.assertEqual(self.contract["contract_version"], "2.0.0")
        self.assertEqual(self.contract["design_system_version"], "2.0.0")
        electorate_map = next(
            row
            for row in self.contract["visualisations"]
            if row["visualisation_id"] == "electorate_map"
        )
        interaction = electorate_map["interaction"]
        self.assertEqual(interaction["zoom_range"], [1, 40])
        self.assertIn("pinch", interaction["zoom_inputs"])
        self.assertIn("electorate_finder", interaction["focus_inputs"])
        self.assertEqual(len(interaction["inset_views"]), 8)

    def test_map_camera_supports_every_practical_input_and_persists(self):
        self.assertIn("export const MAX_MAP_ZOOM = 40", self.map_source)
        self.assertIn('slider.max = String(MAX_MAP_ZOOM * 100)', self.map_source)
        self.assertIn('addEventListener("wheel"', self.map_source)
        self.assertIn('addEventListener("dblclick"', self.map_source)
        self.assertIn('addEventListener("pointerdown"', self.map_source)
        self.assertIn("pinchDistance", self.map_source)
        self.assertIn('event.key === "ArrowLeft"', self.map_source)
        self.assertIn("options.initialCamera", self.map_source)
        self.assertIn("options.onCameraChange", self.map_source)
        self.assertIn("focusSeat", self.map_source)

    def test_city_insets_electorate_finder_and_labels_are_rendered(self):
        self.assertIn("export function renderMapViewThumbnails", self.map_source)
        self.assertIn('createElementNS(SVG_NS, "svg")', self.map_source)
        self.assertIn('id="pr-map-insets"', self.results_source)
        self.assertIn('id="pr-map-electorate-focus"', self.results_source)
        self.assertIn("populateMapElectorates", self.results_source)
        self.assertIn("pr-map-electorate-label", self.map_source)
        self.assertIn("Enlarge on map", self.results_source)
        self.assertIn("Back to Australia", self.map_source)

    def test_controls_are_responsive_and_do_not_depend_on_colour(self):
        self.assertIn(".pr-map-insets", self.css)
        self.assertIn(".pr-map-zoom-slider", self.css)
        self.assertIn(".pr-map-electorate-label.is-visible", self.css)
        self.assertIn("@media (max-width: 760px)", self.css)
        self.assertIn("scroll-snap-type", self.css)
        self.assertIn('setAttribute("aria-label", label)', self.map_source)
        self.assertIn('setAttribute("aria-pressed"', self.map_source)


if __name__ == "__main__":
    unittest.main()
