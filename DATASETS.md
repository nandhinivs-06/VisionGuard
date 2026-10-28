# Public video datasets used

All of these are public downloads with no registration. `python -m dataset_prep.download` fetches them into `data/raw/<source>/`. The total is about 3 GB, and an interrupted download resumes where it stopped.

| Source key | Dataset | Videos used (pos / neg) | Content | Licence / terms | Where from |
|---|---|---|---|---|---|
| `firesense` | **FIRESENSE** (Grammalidis, Dimitropoulos, Çetin, 2017) | 24 / 25 | Flame: 11 positive, 16 negative. Smoke: 13 positive, 9 negative. | CC-BY-4.0 | Zenodo record 836749 |
| `kmu` | **KMU Fire & Smoke Database** (Ko et al., Keimyung Univ.) | 28 / 10 | Indoor/outdoor flame (22), indoor smoke (2), wildfire smoke (4), smoke- or flame-like moving objects (10, negatives). **One of the source datasets of ONFIRE2023.** | Research use, cite the paper | https://cvpr.kmu.ac.kr/Dataset/Dataset.htm |
| `visifire` | **Bilkent VisiFire** sample clips (Çetin et al.) | 31 / 3 | Fire, forest smoke, smoke, tunnel-accident smoke. Negatives are car lights and traffic. | Academic use | http://signal.ee.bilkent.edu.tr/VisiFire/Demo/ |
| `ustc_vsd` | **USTC video smoke detection** (Feiniu Yuan) | 3 / 3 | Smoke (dry leaf, cotton rope, black smoke). Non-smoke: traffic, basketball yard, waving leaves. | Research use only, no commercial use | http://staff.ustc.edu.cn/~yfn/vsd.html |
| `hf_fire_videos` | smolvlm2-fire-videos | 9 / 5 | Short fire and non-fire clips | **No licence given.** Some clips look like stock-footage previews: internal testing only, do not redistribute | Hugging Face `zwanderer/smolvlm2-fire-videos` |
| | **Total** | **95 / 46 = 141 videos, 2.7 h** | | | |

`python -m dataset_prep.build_videos` merges them into `data/raw/videos/` as `<source>__<name>`. Along the way it:
- **Removes exact duplicates** by content hash. Two VisiFire clips appear in two folders.
- **Skips VisiFire's `*_Output.avi` clips.** Those are the authors' algorithm results with detections drawn on the frames.
- **Writes an ONFIRE-style ground-truth `.txt` per video.** `0` means fire is present from the first frame; an empty file means a negative video.

**Limitation:** these datasets only label *whether* a video has fire or smoke, not *when* it starts. So positive videos use start time 0. A few clips show the fire or smoke starting later (for example the VisiFire tunnel accidents). If you know the real start time, put it in seconds in that video's `.txt` in `data/raw/videos/`, then rerun from `organize`.

## Looked for but not included

| Dataset | Reason |
|---|---|
| ONFIRE2023 (MIVIA) | Only available by emailing the organisers; mivia.unisa.it was unreachable |
| MIVIA fire / smoke video datasets | Server unreachable (connection refused). The Hugging Face "MIVIA" repos are an unrelated *audio* dataset |
| D-Fire surveillance videos | OneDrive folder that needs a sign-in |
| Kaggle / Roboflow video sets | Need an account and API key |
| RISE | No public download link found |

## Citations

- N. Grammalidis, K. Dimitropoulos, E. Çetin, *FIRESENSE database of videos for flame and smoke detection*, Zenodo, 2017. https://doi.org/10.5281/zenodo.836749
- K. Dimitropoulos, P. Barmpoutis, N. Grammalidis, "Spatio-Temporal Flame Modeling and Dynamic Texture Analysis for Automatic Video-Based Fire Detection", *IEEE TCSVT*, 25(2), 2015.
- B. C. Ko, S. J. Ham, J. Y. Nam, "Modeling and Formalization of Fuzzy Finite Automata for Detection of Irregular Fire Flames", *IEEE TCSVT*, 21(12), 2011. (KMU)
- B. C. Ko, J. Y. Kwak, J. Y. Nam, "Wildfire Smoke Detection using Temporal-Spatial Features and Random Forest Classifiers", *Optical Engineering*, 51(1), 2012. (KMU)
- B. U. Töreyin, Y. Dedeoğlu, U. Güdükbay, A. E. Çetin, "Computer vision based method for real-time fire and flame detection", *Pattern Recognition Letters*, 2006. (VisiFire)
- F. Yuan, video smoke detection videos, USTC, http://staff.ustc.edu.cn/~yfn/vsd.html
