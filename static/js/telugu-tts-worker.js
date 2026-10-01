const voiceId = 'te_IN-maya-medium';
const voicePath = 'te/te_IN/maya/medium/te_IN-maya-medium.onnx';
const modelHost = 'https://huggingface.co/';
const modelPathPrefix = 'rhasspy/piper-voices/resolve/b23e00315cccfa7360b3a7f89b95da786efc4656';
const modelCacheName = 'signal-telugu-piper-config-v1';
let ttsSession = null;
let initializationPromise = null;
let initializationAbortController = null;

async function readResource(url, cache, signal) {
  try {
    const cachedResponse = await cache?.match(url);
    if (cachedResponse) return cachedResponse.blob();
  } catch {
    // Fetch from the origin if persistent browser storage is unavailable.
  }

  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`Telugu voice resource download failed (${response.status}).`);

  const contentLength = Number(response.headers.get('Content-Length') || 0);
  const reader = response.body?.getReader();
  if (!reader) return response.blob();

  const chunks = [];
  let loaded = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    if (contentLength > 0) self.postMessage({ type: 'progress', loaded, total: contentLength });
  }

  const blob = new Blob(chunks, { type: response.headers.get('Content-Type') || undefined });
  try {
    await cache?.put(url, new Response(blob.clone()));
  } catch {
    // The downloaded model remains available to this worker session.
  }
  return blob;
}

async function initializeSession() {
  if (!initializationPromise) {
    initializationAbortController = new AbortController();
    initializationPromise = (async () => {
      const piper = await import('/static/vendor/piper-tts-web/piper-tts-web.js');
      let cache = null;
      try {
        if ('caches' in self) cache = await caches.open(modelCacheName);
      } catch {
        cache = null;
      }

      const fileReader = new piper.CachedFileReader({
        pathMap: { ...piper.PATH_MAP, [voiceId]: voicePath },
        modelHost,
        modelPathPrefix,
        cacheFolder: 'signal-telugu-piper',
        progress: (progress) => self.postMessage({ type: 'progress', ...progress }),
        customLoader: async (url) => {
          if (!url.endsWith('.onnx.json')) return null;
          const resource = await readResource(url, cache, initializationAbortController.signal);
          const config = JSON.parse(await resource.text());
          config.speaker_id_map ??= {};
          return new Blob([JSON.stringify(config)], { type: 'application/json' });
        },
      });

      ttsSession = await piper.TtsSession.create({
        voiceId,
        fileReader,
        allowLocalModels: true,
        fallbackStrategy: 'local',
        wasmPaths: {
          onnxWasm: '/static/vendor/onnxruntime/',
          piperData: '/static/vendor/piper-wasm/piper_phonemize.data',
          piperWasm: '/static/vendor/piper-wasm/piper_phonemize.wasm',
        },
      });
    })();
  }
  return initializationPromise;
}

self.addEventListener('message', async ({ data }) => {
  try {
    if (data.type === 'initialize') {
      await initializeSession();
      self.postMessage({ id: data.id });
      return;
    }

    if (data.type === 'synthesize') {
      await initializeSession();
      const audio = await ttsSession.predict(data.text);
      self.postMessage({ id: data.id, audio });
    }
  } catch (error) {
    self.postMessage({ id: data.id, error: error.message || 'Telugu speech could not be generated.' });
  }
});