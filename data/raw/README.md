# data/raw

The raw catalogue currently lives at the project root as `BigBasket.csv` and is
used in place, so there is no duplicate copy. The pipeline only reads it and checks its
SHA-256 checksum before and after each run.

To keep raw data here instead, move the file to `data/raw/BigBasket.csv` and set
`"source_csv": "data/raw/BigBasket.csv"` in `config/product_selection.json`.
