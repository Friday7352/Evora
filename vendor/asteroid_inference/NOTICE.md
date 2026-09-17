# Asteroid inference subset
Source: https://github.com/asteroid-team/asteroid/tree/fce87469132760fbab41c20616ea0f0e079aad38

Original license and in-source attributions retained. Only DPRNN, its supporting
normalization/activation/complex utilities, and tensor padding are shipped.
recurrent.py retains SingleRNN, MulCatRNN, DPRNNBlock, and DPRNN unchanged;
unused imports and classes were removed. Package initializers export inference
components only. This avoids training metrics, PESQ native compilation and the
released package's incompatible torchmetrics pin. No model weights are bundled.
