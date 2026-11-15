# Fine-tuning Depth Anything V2 Small on Curiosity Navcam

> [!WARNING]
> **AUDIT NOTICE & LEAKAGE CORRECTION (October 2026):**
> An adversarial audit (`docs/AUDIT_3D_PIPELINE.md`) confirmed that this historical experiment contained **prohibited sol leakage**: **Sol 1400 was present in both `--train` (94 photos) and `--test` (37 photos)**.
> Because Sol 1400 was seen during training, the reported aggregate metric reduction (from 4.1% to 3.7%) cannot be claimed as proof of generalizable depth improvement on unseen Martian terrain.
>
> When the splits are evaluated independently:
> - **Leaked Sol 1400 (18 photos):** Fine-tuned model improved over pretrained (median error: 3.79% -> 3.10%; better on 15/18 photos).
> - **Truly Unseen Sol 1037 (19 photos):** Fine-tuned model **regressed** against pretrained baseline (median error: 6.96% -> 7.67%; worse on 9/19 photos).
>
> **Production Decision:** The deployed 3D viewer uses **zero-shot pretrained Depth Anything V2 Small** with per-frame stereo scale-and-shift calibration. The fine-tuned weights are experimental and not integrated into production.

## Historical Experiment Log (Preserved for Traceability)

- Configuration: Trained on sols 520, 700, 1400, 1094, 1098, 1100 (neck + head only, 12 epochs, scale-and-shift-invariant loss).
- Evaluated on test set containing sols 1400 (leaked) and 1037 (unseen).
- Metric: median % error against NASA stereo on held-out pixels, after scaling to metres on calibration pixels.

| Model | One plain fit | Fit chosen per photo | 0-4 m | 4-8 m | 8-15 m | 15-30 m | 30-60 m |
|---|---|---|---|---|---|---|---|
| Pretrained (no training) | 4.6% | 4.1% | 2.3% | 4.7% | 6.1% | 7.9% | 13.3% |
| Fine-tuned on Navcam | 3.7% | 3.7% | 2.5% | 4.3% | 4.1% | 8.8% | 13.7% |

*Aggregate claim: Fine-tuned was better on 25 of 37 test photos (driven by 15/18 on leaked Sol 1400).*
