import hashlib
import json
import struct
import unittest
import zipfile

import yaml

from politica_erd.build import PROJECT_ROOT


class Stage133ElectorateMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract_path = PROJECT_ROOT / "config" / "electorate_boundaries_2025.yml"
        cls.contract = yaml.safe_load(cls.contract_path.read_text(encoding="utf-8"))
        cls.source = PROJECT_ROOT / cls.contract["source"]["source_archive_path"]
        cls.geometry = PROJECT_ROOT / cls.contract["derived_geometry"]["source_path"]
        cls.geojson = json.loads(cls.geometry.read_text(encoding="utf-8"))

    @staticmethod
    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_official_aec_source_archive_is_pinned_and_contains_150_divisions(self):
        source = self.contract["source"]
        self.assertEqual(
            source["download_url"],
            "https://www.aec.gov.au/Electorates/files/2025/AUS-March-2025-esri.zip",
        )
        self.assertEqual(source["source_page_election_label"], 2025)
        self.assertEqual(self.sha256(self.source), source["source_archive_sha256"])
        self.assertEqual(self.source.stat().st_size, source["source_archive_size_bytes"])
        with zipfile.ZipFile(self.source) as archive:
            self.assertEqual(set(archive.namelist()), set(source["source_components"]))
            for name, expected in source["source_components"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), expected)
            dbf = archive.read("AUS_ELB_region.dbf")
            shp = archive.read("AUS_ELB_region.shp")
        self.assertEqual(struct.unpack("<I", dbf[4:8])[0], 150)
        self.assertEqual(struct.unpack(">I", shp[0:4])[0], 9994)
        # AEC publishes the divisions as PolygonZ (15); the governed browser
        # derivative intentionally drops unused Z values.
        self.assertEqual(struct.unpack("<I", shp[32:36])[0], 15)

    def test_derived_geojson_is_complete_unique_and_checksum_pinned(self):
        derived = self.contract["derived_geometry"]
        self.assertEqual(self.sha256(self.geometry), derived["sha256"])
        self.assertEqual(self.geometry.stat().st_size, derived["size_bytes"])
        features = self.geojson["features"]
        self.assertEqual(len(features), 150)
        names = [item["properties"]["electorate"].casefold() for item in features]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(
            {item["geometry"]["type"] for item in features},
            {"Polygon", "MultiPolygon"},
        )
        self.assertTrue({"Bean", "Bullwinkel", "Lingiari", "Solomon"}.issubset(
            {item["properties"]["electorate"] for item in features}
        ))

    def test_visualisation_contract_and_frontend_publish_accessible_map(self):
        contract = yaml.safe_load(
            (PROJECT_ROOT / "config" / "visualisation_contract.yml").read_text(
                encoding="utf-8"
            )
        )
        registered = {
            item["visualisation_id"]: item for item in contract["visualisations"]
        }
        map_contract = registered["electorate_map"]
        self.assertEqual(contract["contract_version"], "2.0.0")
        self.assertEqual(map_contract["status"], "available")
        self.assertEqual(map_contract["route_id"], "house")
        self.assertEqual(map_contract["component"], "accessible-svg-map")
        self.assertEqual(map_contract["required_feeds"], ["house_seat_results"])
        source = (PROJECT_ROOT / "visualisation/src/components/results.js").read_text(
            encoding="utf-8"
        )
        map_source = (PROJECT_ROOT / "visualisation/src/foundation/map.js").read_text(
            encoding="utf-8"
        )
        css = (PROJECT_ROOT / "visualisation/src/politica.css").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="pr-house-map"', source)
        self.assertIn('setAttribute("role", "button")', map_source)
        self.assertIn('setAttribute("tabindex", "0")', map_source)
        self.assertIn('event.key === "Enter"', map_source)
        self.assertIn(".pr-map-electorate.is-selected", css)
        self.assertIn("Australian Electoral Commission", source)


if __name__ == "__main__":
    unittest.main()
