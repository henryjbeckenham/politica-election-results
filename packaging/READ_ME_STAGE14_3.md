# Stage 14.3 installation

Download the core update ZIP and all four numbered data ZIPs. Keep the four data ZIPs in Downloads. Extract only the core update ZIP, then run:

```bash
cd ~/Downloads/Politica_Stage14_3_v1.5.0_Update
chmod +x install_stage14_3.command
./install_stage14_3.command
```

The data ZIPs do not need to be extracted manually. If Safari has already extracted one, the installer also recognises its extracted folder.

The installer verifies the four package checksums, all 45 original 2019 AEC source checksums, every prevalidated table and Parquet shard, the unchanged 2025 and 2022 source identities, the 2019 reconciliation counts, and all three electorate boundary contracts. It then builds the three-election static website.

The full Python, browser, and clean-install regression suites were run before packaging and are not repeated on the Mac.
