# Vendored Telugu TTS Runtime

The vendored runtime files are distribution assets, with one worker-compatibility change: `piper-tts-web.js` imports the local ONNX Runtime bundle by URL because browser import maps do not apply inside module workers.

- `@jtsage/piper-tts-web` 1.2.0, MIT: https://github.com/jtsage/piper-tts-web
- `@diffusionstudio/piper-wasm` 1.0.0, MIT: https://github.com/diffusion-studio/piper-wasm
- `onnxruntime-web` 1.22.0, MIT: https://github.com/microsoft/onnxruntime/tree/v1.22.0

The Telugu `te_IN-maya-medium` ONNX voice weights are not bundled. The browser downloads them on first Telugu playback from the pinned `rhasspy/piper-voices` revision `b23e00315cccfa7360b3a7f89b95da786efc4656` and caches the model locally. The voice model card links the IIT Madras IndicTTS dataset license: https://www.iitm.ac.in/donlab/indictts/downloads/license.pdf