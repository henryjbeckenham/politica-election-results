# Stage 14.4 installation

Download the core update ZIP and all four numbered data ZIPs. Keep the four data ZIPs in Downloads. Extract only the core update ZIP, then run:

```bash
cd ~/Downloads/Politica_Stage14_4_v1.6.0_Update
chmod +x install_stage14_4.command
./install_stage14_4.command
```

The data ZIPs do not need to be extracted manually. If Safari has already extracted one, the installer also recognises its extracted folder.

The installer verifies the four package checksums, all 46 original 2016 AEC source checksums, every prevalidated table and Parquet shard, the unchanged 2025, 2022 and 2019 source identities, the 2016 reconciliation counts and all four electorate boundary contracts. It then builds the four-election static website.

The full Python, browser, and clean-install regression suites were run before packaging and are not repeated on the Mac.
