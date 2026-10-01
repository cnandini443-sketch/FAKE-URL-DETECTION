# Dataset Source

- Dataset: PhiUSIIL Phishing URL (Website) Dataset
- Authors: Arvind Prasad and Shalini Chandra (2024)
- Repository: [UCI Machine Learning Repository](https://archive.ics.uci.edu/dataset/967/phiusiil+phishing+url+dataset)
- Archive: [Official UCI download](https://archive.ics.uci.edu/static/public/967/phiusiil+phishing+url+dataset.zip)
- DOI: https://doi.org/10.1016/j.cose.2023.103545
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/)
- Retrieved: 2026-09-29

`urls.csv` is a derived training file containing the source `URL` values unchanged and the source labels mapped to the training pipeline's textual labels. The UCI documentation defines source label `1` as legitimate and `0` as phishing; therefore the conversion is `1 -> Legitimate` and `0 -> Phishing`. The complete source dataset contains 235,795 rows: 134,850 legitimate and 100,945 phishing.

The training pipeline subsequently removes invalid or duplicate normalized URLs. No model was trained while preparing this file.
