# DeepCW engine model

`model.onnx` and `model.onnx.json` are the CW decoding model from
[deepcw-engine](https://github.com/e04/deepcw-engine) (commit
`8e264d243bbd4467bd19f3f28292219405b47e0e`), the model behind the
[DeepCW](https://github.com/e04/web-deep-cw-decoder) web decoder.

They are distributed under the GNU Affero General Public License v3.0,
see `LICENSE` in this directory. This differs from the MIT license of the
rest of CAESAR Desktop; builds that include these files are distributed
under AGPL-3.0 as a whole — see the top-level `LICENSE`.

`morse/deepcw_engine.py` is a Python port of the engine's reference
pre-processing and CTC decoding, also under AGPL-3.0-only.

`morse/decoder.py` (MIT, like the rest of CAESAR Desktop) is an
independent Python implementation of the streaming approach used by the
DeepCW web app.
