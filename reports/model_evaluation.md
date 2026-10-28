# Model evaluation (Member 2)

Selected model: **base**, chosen on F1 over the *validation* video frames. It is saved as `models/fire_smoke.pt` and `models/fire_smoke.onnx`.

## 1. Image level: D-Fire test set (4,306 images, bounding boxes)

| model | class | Precision | Recall | F1 | mAP50 | mAP50-95 |
|---|---|---|---|---|---|---|
| base | all | 0.766 | 0.688 | 0.725 | 0.755 | 0.430 |
| base | smoke | 0.808 | 0.766 | 0.787 | 0.816 | 0.499 |
| base | fire | 0.724 | 0.610 | 0.662 | 0.694 | 0.362 |
| finetuned | all | 0.764 | 0.686 | 0.723 | 0.755 | 0.427 |
| finetuned | smoke | 0.797 | 0.770 | 0.783 | 0.816 | 0.496 |
| finetuned | fire | 0.732 | 0.603 | 0.661 | 0.694 | 0.359 |

Confusion matrices: `reports/confusion_matrix_base.png`, `reports/confusion_matrix_finetuned.png`

## 2. Frame level: our CCTV videos (conf >= 0.5, unseen during training)

A frame counts as positive if any fire or smoke box is found.

| model | split | frames | Precision | Recall | F1 | false-positive rate | accuracy | negative videos with a false positive |
|---|---|---|---|---|---|---|---|---|
| base | val | 1403 | 0.993 | 0.309 | 0.471 | 0.004 | 0.548 | 1 |
| base | test | 1284 | 0.993 | 0.431 | 0.601 | 0.010 | 0.559 | 1 |
| finetuned | val | 1403 | 0.996 | 0.261 | 0.414 | 0.002 | 0.518 | 1 |
| finetuned | test | 1284 | 1.000 | 0.347 | 0.515 | 0.000 | 0.497 | 0 |

Video-level system metrics (false alarms per hour, alert delay, missed events, FPS) are in `reports/system_evaluation.md`, produced by `python -m system_test.evaluate_system`.
