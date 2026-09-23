# DeepCW engine model

`model.onnx` and `model.onnx.json` are the CW decoding model from
[deepcw-engine](https://github.com/e04/deepcw-engine) (commit
`8e264d243bbd4467bd19f3f28292219405b47e0e`), the model behind the
[DeepCW](https://github.com/e04/web-deep-cw-decoder) web decoder.

They are distributed under the GNU Affero General Public License v3.0,
see `LICENSE` in this directory. Note that this differs from the MIT
license of the rest of CAESAR Desktop.

`morse/deepcw_engine.py` is a Python port of the engine's reference
pre-processing and CTC decoding; `morse/decoder.py` ports the streaming
segmentation used by the DeepCW web app (`useStreamingDecode.ts`).
