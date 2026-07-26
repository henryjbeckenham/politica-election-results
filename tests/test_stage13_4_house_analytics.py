import unittest

import yaml

from politica_erd.build import PROJECT_ROOT


class Stage134HouseAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = yaml.safe_load(
            (PROJECT_ROOT / "config" / "visualisation_contract.yml").read_text(
                encoding="utf-8"
            )
        )
        cls.results_source = (
            PROJECT_ROOT / "visualisation/src/components/results.js"
        ).read_text(encoding="utf-8")
        cls.analytics_source = (
            PROJECT_ROOT / "visualisation/src/foundation/analytics.js"
        ).read_text(encoding="utf-8")
        cls.map_source = (
            PROJECT_ROOT / "visualisation/src/foundation/map.js"
        ).read_text(encoding="utf-8")
        cls.url_source = (
            PROJECT_ROOT / "visualisation/src/foundation/url-state.js"
        ).read_text(encoding="utf-8")
        cls.css = (PROJECT_ROOT / "visualisation/src/politica.css").read_text(
            encoding="utf-8"
        )
        cls.compiled_root = PROJECT_ROOT / "src/politica_erd/app/results"

    def test_analytics_route_and_all_stage13_4_components_are_governed(self):
        self.assertEqual(self.contract["contract_version"], "2.0.0")
        routes = {row["route_id"]: row for row in self.contract["routes"]}
        self.assertEqual(routes["analytics"]["status"], "available")
        registered = {
            row["visualisation_id"]: row
            for row in self.contract["visualisations"]
        }
        expected = {
            "electoral_pendulum",
            "closest_contests",
            "house_swing_analysis",
            "party_gains_losses",
            "vote_seat_comparison",
            "state_comparison",
            "vote_type_comparison",
        }
        self.assertTrue(expected.issubset(registered))
        for visualisation_id in expected:
            self.assertEqual(registered[visualisation_id]["status"], "available")
            self.assertEqual(registered[visualisation_id]["route_id"], "analytics")
        self.assertEqual(
            registered["house_swing_analysis"]["metrics"],
            ["reported_tcp_swing"],
        )

    def test_house_analytics_use_only_existing_fixed_public_feeds(self):
        self.assertIn("export function marginSpectrumRows", self.analytics_source)
        self.assertIn("export function tcpSwingRows", self.analytics_source)
        self.assertIn("export function voteSeatRows", self.analytics_source)
        self.assertIn("export function partyGainLossRows", self.analytics_source)
        self.assertIn('rows("house_seat_results")', self.results_source)
        self.assertIn('rows("house_candidate_results")', self.results_source)
        self.assertNotIn("fetch(", self.analytics_source)
        self.assertIn("AEC-reported", self.results_source)
        self.assertIn("not a recalculation", self.results_source)

    def test_map_has_general_zoom_pan_reset_and_capital_city_views(self):
        map_filter = next(
            row for row in self.contract["filters"] if row["filter_id"] == "map_view"
        )
        for value in (
            "SYDNEY",
            "MELBOURNE",
            "BRISBANE",
            "ADELAIDE",
            "PERTH",
            "HOBART",
            "CANBERRA",
            "DARWIN",
        ):
            self.assertIn(value, map_filter["values"])
            self.assertIn(f"{value}:", self.map_source)
        self.assertIn('event.key === "0"', self.map_source)
        self.assertIn('addEventListener("wheel"', self.map_source)
        self.assertIn('addEventListener("pointermove"', self.map_source)
        self.assertIn('"Reset view"', self.map_source)
        self.assertIn('source.searchParams.get("map_view")', self.url_source)
        self.assertIn(".pr-map-toolbar", self.css)
        self.assertIn("touch-action: none", self.css)

    def test_analysis_interface_is_accessible_selectable_and_responsive(self):
        self.assertIn('data-view="analytics"', self.results_source)
        self.assertIn('id="pr-margin-spectrum"', self.results_source)
        self.assertIn('id="pr-swing-ranking"', self.results_source)
        self.assertIn('id="pr-vote-seat"', self.results_source)
        self.assertIn('button.setAttribute("aria-label"', self.results_source)
        self.assertIn(".pr-analytics-grid", self.css)
        self.assertIn("@media (max-width: 760px)", self.css)

    def test_compiled_operator_site_matches_the_verified_frontend_build(self):
        built_root = PROJECT_ROOT / "visualisation/dist"
        built_files = {
            path.relative_to(built_root).as_posix()
            for path in built_root.rglob("*")
            if path.is_file()
        }
        operator_files = {
            path.relative_to(self.compiled_root).as_posix()
            for path in self.compiled_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(operator_files, built_files)
        compiled_javascript = "\n".join(
            path.read_text(encoding="utf-8")
            for path in self.compiled_root.rglob("*.js")
        )
        self.assertIn("Margins, movement and representation", compiled_javascript)
        self.assertIn("Sydney metropolitan area", compiled_javascript)
        self.assertIn("export function voteSeatRows", self.analytics_source)


if __name__ == "__main__":
    unittest.main()
