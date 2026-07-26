# Stage 14.2 installation

Download the core update ZIP and all four numbered data ZIPs. Keep the four data ZIPs in Downloads. Extract only the core update ZIP, then run:

```bash
cd ~/Downloads/Politica_Stage14_2_v1.4.0_Update
chmod +x install_stage14_2.command
./install_stage14_2.command
```

The data ZIPs do not need to be extracted manually. If Safari has already extracted one, the installer also recognises its extracted folder.

The installer verifies the four package checksums, all 45 original AEC source checksums, every prevalidated table and Parquet shard, the unchanged 2025 source identity, the 2022 reconciliation counts and the 150/151 electorate boundary contracts. It then builds the two-election static website.

The full Python, browser and clean-install regression suites were run before packaging and are not repeated on the Mac.
