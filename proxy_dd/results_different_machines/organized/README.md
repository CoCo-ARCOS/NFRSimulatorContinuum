# Organized Machine Results

This directory reorganizes `results_different_machines` into machine-specific datasets that mirror the structure of `real_values`.

Machine mapping:

- `c3`: HPC machine
- `toge`: Raspberry Pi 5
- `dianalap`: Personal laptop

Each machine folder contains a `real_values/` directory with:

- `cost-efficiency.csv`
- `integrity.csv`
- `confidentiality.csv`
- `reliability.csv`

Notes:

- `confidentiality.csv` is additional to the original `real_values` layout because the raw machine benchmarks include encryption timings separately.
- `c3` security and reliability results were merged from split benchmark runs covering `1..100 MB` and `1000 MB`.

Coverage summary:

| Machine | Profile | Dataset | Rows | Sizes (MB) | Algorithms |
|---|---|---:|---:|---|---|
| C3 | hpc | cost-efficiency.csv | 25 | 1|10|50|100|1000 | BZ2|LZ4|LZMA|ZLIB|ZSTD |
| C3 | hpc | integrity.csv | 20 | 1|10|50|100|1000 | BLAKE3|HMAC_SHA256|SHA256|SHA3_256 |
| C3 | hpc | confidentiality.csv | 15 | 1|10|50|100|1000 | AES|CHACHA20 |
| C3 | hpc | reliability.csv | 15 | 1|10|50|100|1000 | RS |
| Toge | raspberry_pi_5 | cost-efficiency.csv | 25 | 1|10|50|100|1000 | BZ2|LZ4|LZMA|ZLIB|ZSTD |
| Toge | raspberry_pi_5 | integrity.csv | 20 | 1|10|50|100|1000 | BLAKE3|HMAC_SHA256|SHA256|SHA3_256 |
| Toge | raspberry_pi_5 | confidentiality.csv | 15 | 1|10|50|100|1000 | AES|CHACHA20 |
| Toge | raspberry_pi_5 | reliability.csv | 15 | 1|10|50|100|1000 | RS |
| DianaLap | personal_laptop | cost-efficiency.csv | 25 | 1|10|50|100|1000 | BZ2|LZ4|LZMA|ZLIB|ZSTD |
| DianaLap | personal_laptop | integrity.csv | 20 | 1|10|50|100|1000 | BLAKE3|HMAC_SHA256|SHA256|SHA3_256 |
| DianaLap | personal_laptop | confidentiality.csv | 15 | 1|10|50|100|1000 | AES|CHACHA20 |
| DianaLap | personal_laptop | reliability.csv | 15 | 1|10|50|100|1000 | RS |
