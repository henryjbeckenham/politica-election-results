# Stage 14.6 installation

Download the core update ZIP and all four numbered data ZIPs. Keep the four data ZIPs in Downloads. Extract only the core update ZIP, then run:

```bash
cd ~/Downloads/Politica_Stage14_6_v1.8.0_Update
chmod +x install_stage14_6.command
./install_stage14_6.command
```

The data ZIPs do not need to be extracted manually. If Safari has already extracted one, the installer also recognises its extracted folder.

The installer verifies the four package checksums, all 47 original 2010 AEC source checksums, every prevalidated table and Parquet shard, the unchanged 2025, 2022, 2019, 2016 and 2013 election identities, the 2010 reconciliation counts and all six electorate boundary contracts. It then builds the six-election static website.

The full Python, browser and clean-install regression suites were run before packaging and are not repeated on the Mac.
