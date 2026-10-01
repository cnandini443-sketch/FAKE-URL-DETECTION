const pageNames = { scan: 'URL scanner', dashboard: 'Dashboard', history: 'Scan history' };
const byId = (id) => document.getElementById(id);

let currentResult = null;
let currentExplanationLanguage = 'en';
let explanationTranscriptElement = null;
let activeVoiceLang = null;
let voiceStatusMessage = '';
let speechRequestId = 0;
let activeAudio = null;
let activeAudioUrl = null;
let piperTtsSessionPromise = null;
let piperTtsWorker = null;
let piperTtsWorkerBusy = false;
let piperTtsWorkerRequestId = 0;
const piperTtsPendingRequests = new Map();
let piperTtsFailureMessage = '';
const TELUGU_TTS_PREPARATION_TIMEOUT_MS = 60000;
const TELUGU_TTS_SYNTHESIS_TIMEOUT_MS = 20000;
let activeQrUrl = null;
let cameraStream = null;
let cameraScanLoopId = null;

async function apiRequest(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('The server returned an unreadable response.');
  }
  if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
  return data;
}

function setMessage(element, message, type = 'error') {
  element.textContent = message;
  element.className = `inline-message ${type}`;
  element.hidden = false;
}

function makeElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatScore(score) {
  const num = typeof score === 'number' ? score : parseFloat(score);
  return Number.isNaN(num) ? '0.0' : num.toFixed(1);
}

function setView(name) {
  for (const view of document.querySelectorAll('.view')) {
    const isActive = view.id === `view-${name}`;
    view.hidden = !isActive;
    view.classList.toggle('active', isActive);
  }
  for (const button of document.querySelectorAll('.nav-link')) {
    button.classList.toggle('active', button.dataset.view === name);
  }
  byId('page-title').textContent = pageNames[name];
  if (name === 'dashboard') loadDashboard();
  if (name === 'history') loadHistory();
}

/* ==================================================
   TAB SWITCHING: MANUAL URL vs QR CODE
================================================== */
const tabManual = byId('tab-manual');
const tabQr = byId('tab-qr');
const sectionManual = byId('section-manual');
const sectionQr = byId('section-qr');

function switchScannerTab(mode) {
  if (mode === 'qr') {
    tabManual.classList.remove('active');
    tabManual.setAttribute('aria-selected', 'false');
    tabQr.classList.add('active');
    tabQr.setAttribute('aria-selected', 'true');
    sectionManual.hidden = true;
    sectionManual.classList.remove('active');
    sectionQr.hidden = false;
    sectionQr.classList.add('active');
  } else {
    tabQr.classList.remove('active');
    tabQr.setAttribute('aria-selected', 'false');
    tabManual.classList.add('active');
    tabManual.setAttribute('aria-selected', 'true');
    sectionQr.hidden = true;
    sectionQr.classList.remove('active');
    sectionManual.hidden = false;
    sectionManual.classList.add('active');
    stopCamera();
  }
}

tabManual?.addEventListener('click', () => switchScannerTab('manual'));
tabQr?.addEventListener('click', () => switchScannerTab('qr'));

/* ==================================================
   VOICE EXPLANATION (Web Speech API)
================================================== */
function updateVoiceButtonsUI() {
  const btnEn = byId('voice-btn-en');
  const btnTe = byId('voice-btn-te');
  const btnStop = byId('voice-btn-stop');
  const statusEl = byId('voice-status-indicator');

  if (!btnEn || !btnTe || !btnStop) return;

  btnEn.classList.toggle('speaking', activeVoiceLang === 'en');
  btnTe.classList.toggle('speaking', activeVoiceLang === 'te');
  btnStop.hidden = activeVoiceLang === null;

  if (statusEl) {
    if (activeVoiceLang === 'en') {
      statusEl.textContent = voiceStatusMessage || 'Speaking in English...';
      statusEl.hidden = false;
    } else if (activeVoiceLang === 'te') {
      statusEl.textContent = voiceStatusMessage || 'తెలుగులో మాట్లాడుతోంది...';
      statusEl.hidden = false;
    } else if (voiceStatusMessage) {
      statusEl.textContent = voiceStatusMessage;
      statusEl.hidden = false;
    } else {
      statusEl.hidden = true;
    }
  }
}

function stopVoice() {
  speechRequestId += 1;
  cancelLocalTeluguPreparation();
  if ('speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  if (activeAudio) {
    activeAudio.pause();
    activeAudio.currentTime = 0;
    activeAudio.removeAttribute('src');
    activeAudio.load();
    activeAudio = null;
  }
  if (activeAudioUrl) {
    URL.revokeObjectURL(activeAudioUrl);
    activeAudioUrl = null;
  }
  activeVoiceLang = null;
  voiceStatusMessage = '';
  updateVoiceButtonsUI();
}

function updateVisibleExplanation(language) {
  currentExplanationLanguage = language;
  const text = currentResult?.voice_narration?.[language] || '';
  if (explanationTranscriptElement) {
    explanationTranscriptElement.textContent = text;
    explanationTranscriptElement.lang = language === 'te' ? 'te' : 'en';
  }
  return text;
}

function speechVoiceMatchesLanguage(voice, lang) {
  const voiceLang = voice.lang.toLowerCase();
  return lang === 'te' ? voiceLang === 'te-in' || voiceLang.startsWith('te-') : voiceLang.startsWith('en-');
}

function waitForSpeechVoices(lang) {
  const synthesis = window.speechSynthesis;
  const voices = synthesis.getVoices();
  if (voices.some((voice) => speechVoiceMatchesLanguage(voice, lang))) {
    return Promise.resolve(voices);
  }

  return new Promise((resolve) => {
    let quietTimeoutId;
    let maximumTimeoutId;
    let finished = false;
    const finish = (availableVoices = synthesis.getVoices()) => {
      if (finished) return;
      finished = true;
      window.clearTimeout(quietTimeoutId);
      window.clearTimeout(maximumTimeoutId);
      synthesis.removeEventListener('voiceschanged', onVoicesChanged);
      resolve(availableVoices);
    };
    const scheduleQuietFinish = () => {
      window.clearTimeout(quietTimeoutId);
      quietTimeoutId = window.setTimeout(() => finish(), 2500);
    };
    const onVoicesChanged = () => {
      const availableVoices = synthesis.getVoices();
      if (availableVoices.some((voice) => speechVoiceMatchesLanguage(voice, lang))) {
        finish(availableVoices);
      } else {
        scheduleQuietFinish();
      }
    };
    synthesis.addEventListener('voiceschanged', onVoicesChanged);
    scheduleQuietFinish();
    maximumTimeoutId = window.setTimeout(() => finish(), 8000);
  });
}

function selectSpeechVoice(voices, lang) {
  const matching = voices.filter((voice) => speechVoiceMatchesLanguage(voice, lang));
  if (!matching.length) return null;

  const localeOrder = lang === 'te' ? ['te-in'] : ['en-in', 'en-us', 'en-gb'];
  matching.sort((left, right) => {
    const leftLang = left.lang.toLowerCase();
    const rightLang = right.lang.toLowerCase();
    const leftLocaleRank = localeOrder.indexOf(leftLang);
    const rightLocaleRank = localeOrder.indexOf(rightLang);
    const localeDifference = (leftLocaleRank < 0 ? localeOrder.length : leftLocaleRank)
      - (rightLocaleRank < 0 ? localeOrder.length : rightLocaleRank);
    const quality = /natural|neural|premium|enhanced|google/i;
    if (localeDifference !== 0) return localeDifference;
    const qualityDifference = Number(quality.test(right.name)) - Number(quality.test(left.name));
    return qualityDifference || Number(Boolean(right.default)) - Number(Boolean(left.default));
  });
  return matching[0];
}

function cancelLocalTeluguPreparation() {
  if (piperTtsWorkerBusy) discardLocalTeluguSession();
}

function discardLocalTeluguSession() {
  piperTtsWorker?.terminate();
  piperTtsWorker = null;
  piperTtsWorkerBusy = false;
  piperTtsSessionPromise = null;
  for (const { reject } of piperTtsPendingRequests.values()) {
    reject(new Error('Telugu speech preparation was stopped.'));
  }
  piperTtsPendingRequests.clear();
}

function waitForTeluguOperation(operation, timeoutMs, message) {
  let timeoutId;
  return Promise.race([
    operation,
    new Promise((resolve, reject) => {
      timeoutId = window.setTimeout(() => reject(new Error(message)), timeoutMs);
    }),
  ]).finally(() => window.clearTimeout(timeoutId));
}

function requestLocalTeluguWorker(type, values = {}) {
  return new Promise((resolve, reject) => {
    const id = ++piperTtsWorkerRequestId;
    piperTtsPendingRequests.set(id, { resolve, reject });
    piperTtsWorker.postMessage({ id, type, ...values });
  });
}

function getLocalTeluguSession(requestId) {
  if (!piperTtsSessionPromise) {
    piperTtsWorker = new Worker('/static/js/telugu-tts-worker.js', { type: 'module' });
    piperTtsWorker.onmessage = ({ data }) => {
      if (data.type === 'progress') {
        if (requestId !== speechRequestId) return;
        const percent = data.total > 0 ? ` (${Math.round((data.loaded / data.total) * 100)}%)` : '';
        voiceStatusMessage = `Downloading Telugu voice model${percent}...`;
        updateVoiceButtonsUI();
        return;
      }

      const pending = piperTtsPendingRequests.get(data.id);
      if (!pending) return;
      piperTtsPendingRequests.delete(data.id);
      pending[data.error ? 'reject' : 'resolve'](data.error ? new Error(data.error) : data.audio || true);
    };
    piperTtsWorker.onerror = (event) => {
      const error = new Error(event.message || 'The local Telugu speech worker failed.');
      for (const { reject } of piperTtsPendingRequests.values()) reject(error);
      piperTtsPendingRequests.clear();
      piperTtsWorker?.terminate();
      piperTtsWorker = null;
      piperTtsSessionPromise = null;
      piperTtsWorkerBusy = false;
    };
    piperTtsWorkerBusy = true;
    piperTtsSessionPromise = requestLocalTeluguWorker('initialize').finally(() => {
      piperTtsWorkerBusy = false;
    });
  }
  return piperTtsSessionPromise;
}

function synthesizeLocalTelugu(text) {
  piperTtsWorkerBusy = true;
  return requestLocalTeluguWorker('synthesize', { text }).finally(() => {
    piperTtsWorkerBusy = false;
  });
}

function playLocalTeluguAudio(wav, requestId) {
  if (requestId !== speechRequestId) return;

  const audioUrl = URL.createObjectURL(wav);
  const audio = new Audio(audioUrl);
  activeAudio = audio;
  activeAudioUrl = audioUrl;

  const releaseAudio = () => {
    if (activeAudio === audio) activeAudio = null;
    if (activeAudioUrl === audioUrl) {
      URL.revokeObjectURL(audioUrl);
      activeAudioUrl = null;
    }
  };
  const finish = (message = '') => {
    releaseAudio();
    if (requestId !== speechRequestId) return;
    activeVoiceLang = null;
    voiceStatusMessage = message;
    updateVoiceButtonsUI();
  };

  audio.onended = () => finish();
  audio.onerror = () => finish('Telugu audio playback failed.');
  voiceStatusMessage = 'Telugu speech ready. Playing...';
  updateVoiceButtonsUI();
  audio.play().then(() => {
    if (requestId !== speechRequestId) return;
    voiceStatusMessage = '';
    updateVoiceButtonsUI();
  }).catch(() => finish('Telugu audio playback failed.'));
}

async function speakVoice(lang) {
  const textToSpeak = updateVisibleExplanation(lang);
  if (!textToSpeak) return;

  const hasSpeechSynthesis = 'speechSynthesis' in window;
  if (!hasSpeechSynthesis && lang === 'en') {
    voiceStatusMessage = 'Speech synthesis is not supported by this browser.';
    updateVoiceButtonsUI();
    return;
  }

  // If already speaking the same language, stop it
  if (activeVoiceLang === lang) {
    stopVoice();
    return;
  }

  stopVoice();

  const requestId = speechRequestId;
  activeVoiceLang = lang;
  voiceStatusMessage = lang === 'te' ? 'Preparing Telugu speech...' : 'Preparing speech...';
  updateVoiceButtonsUI();

  if (lang === 'te' && piperTtsFailureMessage) {
    activeVoiceLang = null;
    voiceStatusMessage = piperTtsFailureMessage;
    updateVoiceButtonsUI();
    return;
  }

  const voices = hasSpeechSynthesis ? await waitForSpeechVoices(lang) : [];
  if (requestId !== speechRequestId) return;

  const voice = selectSpeechVoice(voices, lang);
  if (lang === 'te' && !voice) {
    voiceStatusMessage = 'Preparing Telugu speech...';
    updateVoiceButtonsUI();

    try {
      const session = await waitForTeluguOperation(
        getLocalTeluguSession(requestId),
        TELUGU_TTS_PREPARATION_TIMEOUT_MS,
        'Telugu speech preparation timed out. Check your connection and reload to retry.',
      );
      if (requestId !== speechRequestId) return;
      const wav = await waitForTeluguOperation(
        synthesizeLocalTelugu(textToSpeak),
        TELUGU_TTS_SYNTHESIS_TIMEOUT_MS,
        'Telugu speech generation timed out. Please reload and try again.',
      );
      playLocalTeluguAudio(wav, requestId);
    } catch (error) {
      if (requestId !== speechRequestId) return;
      discardLocalTeluguSession();
      const detail = error.message || '';
      piperTtsFailureMessage = detail.includes('timed out')
        ? detail
        : 'Telugu speech unavailable. The local Telugu voice could not be loaded. Check your connection and try again.';
      activeVoiceLang = null;
      voiceStatusMessage = piperTtsFailureMessage;
      updateVoiceButtonsUI();
    }
    return;
  }

  const utterance = new SpeechSynthesisUtterance(textToSpeak);
  utterance.lang = lang === 'te' ? 'te-IN' : voice ? voice.lang : 'en-IN';
  if (voice) utterance.voice = voice;
  utterance.rate = 0.92;

  utterance.onstart = () => {
    if (requestId !== speechRequestId) return;
    voiceStatusMessage = '';
    activeVoiceLang = lang;
    updateVoiceButtonsUI();
  };

  utterance.onend = () => {
    if (requestId !== speechRequestId) return;
    activeVoiceLang = null;
    voiceStatusMessage = '';
    updateVoiceButtonsUI();
  };

  utterance.onerror = (event) => {
    if (requestId !== speechRequestId) return;
    activeVoiceLang = null;
    voiceStatusMessage = `Speech playback failed${event.error ? `: ${event.error}` : '.'}`;
    updateVoiceButtonsUI();
  };

  window.speechSynthesis.speak(utterance);
}

// Pre-load speech synthesis voices
if ('speechSynthesis' in window) {
  window.speechSynthesis.addEventListener('voiceschanged', () => window.speechSynthesis.getVoices());
}

/* ==================================================
   ALERT POPUP / NOTIFICATION MODAL (Requirement 8)
================================================== */
function showAlertModal(result) {
  const popup = byId('alert-popup');
  const modal = byId('popup-modal');
  const icon = byId('popup-icon');
  const title = byId('popup-title');
  const message = byId('popup-message');
  const score = byId('popup-score');
  const levelTag = byId('popup-level-tag');
  const category = byId('popup-category');

  if (!popup || !result) return;

  const alertData = result.alert || {};
  const alertType = alertData.type || (result.risk_level === 'High' ? 'danger' : result.risk_level === 'Medium' ? 'warning' : 'safe');

  modal.className = `alert-popup-modal ${alertType}`;
  title.textContent = alertData.title || (alertType === 'danger' ? '⚠️ PHISHING ALERT' : alertType === 'warning' ? '⚠️ SUSPICIOUS URL' : '✓ LOW RISK');
  message.textContent = alertData.message || (alertType === 'danger'
    ? 'This URL has been identified as high risk. Do not enter passwords, OTPs, card details or other sensitive information.'
    : alertType === 'warning'
    ? 'This URL contains suspicious characteristics. Proceed carefully.'
    : 'No major suspicious indicators detected.');

  category.textContent = alertType === 'danger' ? 'CRITICAL SECURITY THREAT' : alertType === 'warning' ? 'POTENTIAL RISK DETECTED' : 'SYSTEM VERIFIED';
  icon.innerHTML = alertType === 'danger' ? '&#x26A0;' : alertType === 'warning' ? '&#x26A0;' : '&#x2713;';
  score.textContent = formatScore(result.risk_score);
  levelTag.textContent = `${result.risk_level.toUpperCase()} RISK`;
  levelTag.className = `popup-level-tag ${result.risk_level.toLowerCase()}`;

  popup.hidden = false;
}

byId('popup-dismiss')?.addEventListener('click', () => {
  byId('alert-popup').hidden = true;
});

byId('alert-popup')?.addEventListener('click', (e) => {
  if (e.target === byId('alert-popup')) {
    byId('alert-popup').hidden = true;
  }
});

/* ==================================================
   RESULT CARD RENDERING
================================================== */
function renderResult(result) {
  currentResult = result;
  stopVoice();
  currentExplanationLanguage = 'en';

  const content = byId('result-content');
  content.replaceChildren();
  explanationTranscriptElement = null;

  const container = makeElement('div', 'result-container');

  // 1. Top Warning Alert Banner
  const alertBanner = makeElement('div', `result-alert-banner ${result.risk_level.toLowerCase()}`);
  const alertIcon = makeElement('span', 'banner-icon', result.risk_level === 'High' ? '⚠️' : result.risk_level === 'Medium' ? '⚠️' : '✓');
  const alertBody = makeElement('div', 'banner-body');
  const alertHeadRow = makeElement('div', 'banner-head-row');
  const alertTitle = makeElement('strong', '', `${result.risk_level.toUpperCase()} RISK LEVEL`);
  const sourceBadge = makeElement('span', 'source-badge', `Source: ${result.source || 'Manual URL'}`);
  alertHeadRow.append(alertTitle, sourceBadge);
  const alertText = makeElement('p', 'banner-text', result.warning || 'Inspection completed.');
  alertBody.append(alertHeadRow, alertText);
  alertBanner.append(alertIcon, alertBody);

  // 2. Main Metric Cards Grid
  const card = makeElement('div', 'result-card');

  // Card Left: Classification and URL
  const summary = makeElement('div', 'result-main');
  summary.append(makeElement('small', '', 'CLASSIFICATION'));
  const prediction = makeElement('strong', result.prediction.toLowerCase(), result.prediction);
  const urlDisplay = makeElement('span', 'result-url', result.url);
  summary.append(prediction, urlDisplay);

  if (result.explanation && typeof result.explanation.model_probability !== 'undefined') {
    const modelMeta = makeElement('div', 'model-meta-box');
    modelMeta.append(
      makeElement('span', 'meta-label', 'Model Phishing Confidence:'),
      makeElement('strong', 'meta-value', `${formatScore(result.explanation.model_probability)}%`)
    );
    summary.append(modelMeta);
  }

  // Card Center: Numerical Risk Score with Decimals & Visual Meter
  const risk = makeElement('div', 'result-risk');
  risk.append(makeElement('small', '', 'RISK SCORE (0.0 - 100.0)'));
  const number = makeElement('div', 'risk-number');
  const scoreFormatted = formatScore(result.risk_score);
  number.append(document.createTextNode(scoreFormatted), makeElement('span', '', ' / 100'));

  const meter = makeElement('div', 'risk-meter');
  const fill = makeElement('span', result.risk_level.toLowerCase());
  fill.style.width = `${Math.min(100, Math.max(2, result.risk_score))}%`;
  meter.append(fill);

  const riskCaptionRow = makeElement('div', 'risk-caption-row');
  const levelBadge = makeElement('span', `risk-badge ${result.risk_level.toLowerCase()}`, `${result.risk_level.toUpperCase()} RISK`);
  const boundaryText = makeElement('small', 'boundary-hint',
    result.risk_level === 'Low' ? '0.0 – 30.0' : result.risk_level === 'Medium' ? '30.1 – 70.0' : '70.1 – 100.0'
  );
  riskCaptionRow.append(levelBadge, boundaryText);
  risk.append(number, meter, riskCaptionRow);

  // Card Right: Voice Explanation Section
  const voiceBlock = makeElement('div', 'voice-block');
  voiceBlock.append(makeElement('small', '', 'AUDIO VOICE EXPLANATION'));

  const voiceDesc = makeElement('p', 'voice-desc', 'Listen to an automated real-time explanation derived from actual features:');
  const explanationTranscript = makeElement('p', 'voice-desc voice-transcript');
  explanationTranscriptElement = explanationTranscript;
  updateVisibleExplanation(currentExplanationLanguage);

  const voiceButtons = makeElement('div', 'voice-buttons-grid');
  const btnEn = makeElement('button', 'voice-action-btn', '🔊 Explain in English');
  btnEn.id = 'voice-btn-en';
  btnEn.type = 'button';
  btnEn.addEventListener('click', () => speakVoice('en'));

  const btnTe = makeElement('button', 'voice-action-btn', '🔊 తెలుగులో వివరించండి');
  btnTe.id = 'voice-btn-te';
  btnTe.type = 'button';
  btnTe.addEventListener('click', () => speakVoice('te'));

  const btnStop = makeElement('button', 'voice-stop-btn', '⏹ Stop');
  btnStop.id = 'voice-btn-stop';
  btnStop.type = 'button';
  btnStop.hidden = true;
  btnStop.addEventListener('click', stopVoice);

  voiceButtons.append(btnEn, btnTe, btnStop);

  const voiceStatus = makeElement('div', 'voice-status-indicator');
  voiceStatus.id = 'voice-status-indicator';
  voiceStatus.hidden = true;

  voiceBlock.append(voiceDesc, explanationTranscript, voiceButtons, voiceStatus);

  card.append(summary, risk, voiceBlock);

  // 3. Explainable AI (XAI) Breakdown Section
  const xaiSection = makeElement('div', 'xai-container');
  const xaiHeader = makeElement('div', 'xai-header');
  xaiHeader.append(
    makeElement('span', 'eyebrow', 'EXPLAINABLE AI ENGINE'),
    makeElement('h3', '', 'Why This URL Received This Result')
  );
  xaiSection.append(xaiHeader);

  const xaiGrid = makeElement('div', 'xai-grid');

  // XAI Left: Detected Suspicious Indicators
  const detectedBox = makeElement('div', 'xai-box detected-box');
  detectedBox.append(makeElement('h4', '', 'Suspicious Indicators Detected'));
  const detectedList = makeElement('ul', 'xai-list');

  const suspiciousItems = (result.explanation && result.explanation.suspicious_features) || [];
  if (suspiciousItems.length > 0) {
    for (const item of suspiciousItems) {
      const li = makeElement('li', `xai-item sev-${item.severity || 'medium'}`);
      const tag = makeElement('span', `sev-tag ${item.severity || 'medium'}`, (item.severity || 'FLAG').toUpperCase());
      const label = makeElement('strong', '', item.reason || item.name);
      const detail = makeElement('p', 'xai-detail', item.detail);
      li.append(tag, label, detail);
      detectedList.append(li);
    }
  } else {
    const emptyLi = makeElement('li', 'xai-item empty', '✓ No critical structural risk indicators were detected.');
    detectedList.append(emptyLi);
  }
  detectedBox.append(detectedList);

  // XAI Right: Safe Baseline Features & Model Rationale
  const safeBox = makeElement('div', 'xai-box safe-box');
  safeBox.append(makeElement('h4', '', 'Baseline URL Features & Model Analysis'));
  const safeList = makeElement('ul', 'xai-list');

  // Add model reasons first
  for (const reason of (result.reasons || [])) {
    if (reason.includes('Random Forest') || reason.includes('trained model')) {
      const li = makeElement('li', 'xai-item model-rationale');
      const tag = makeElement('span', 'sev-tag model', 'ML MODEL');
      const text = makeElement('p', 'xai-detail', reason);
      li.append(tag, text);
      safeList.append(li);
    }
  }

  const safeItems = (result.explanation && result.explanation.safe_features) || [];
  for (const item of safeItems) {
    const li = makeElement('li', 'xai-item safe-item');
    const tag = makeElement('span', 'sev-tag safe', 'SAFE');
    const label = makeElement('strong', '', item.name);
    const detail = makeElement('p', 'xai-detail', item.detail);
    li.append(tag, label, detail);
    safeList.append(li);
  }
  safeBox.append(safeList);

  xaiGrid.append(detectedBox, safeBox);
  xaiSection.append(xaiGrid);

  container.append(alertBanner, card, xaiSection);
  content.append(container);

  byId('result-panel').hidden = false;
  byId('result-panel').scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Show visual notification modal
  showAlertModal(result);
}

/* ==================================================
   SUBMIT MANUAL URL FORM
================================================== */
byId('scan-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = byId('url-input');
  const button = byId('scan-button');
  const message = byId('scan-message');
  const url = input.value.trim();
  if (!url) {
    input.setCustomValidity('Enter a URL to inspect.');
    input.reportValidity();
    return;
  }
  input.setCustomValidity('');
  message.hidden = true;
  button.disabled = true;
  button.querySelector('span:first-child').textContent = 'Analyzing...';
  try {
    const result = await apiRequest('/api/predict', {
      method: 'POST',
      body: JSON.stringify({ url, source: 'Manual' }),
    });
    renderResult(result);
    byId('model-status').textContent = 'Trained model ready';
    byId('model-dot').classList.add('ready');
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    button.disabled = false;
    button.querySelector('span:first-child').textContent = 'Analyze link';
  }
});

byId('url-input')?.addEventListener('input', (event) => event.currentTarget.setCustomValidity(''));

byId('scan-another')?.addEventListener('click', () => {
  byId('result-panel').hidden = true;
  stopVoice();
  if (!sectionManual.hidden) {
    byId('url-input').focus();
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

/* ==================================================
   QR CODE SCANNER (File Upload & Live Camera)
================================================== */
const qrDropzone = byId('qr-dropzone');
const qrFileInput = byId('qr-file-input');
const qrResultBox = byId('qr-result-box');
const qrDecodedUrl = byId('qr-decoded-url');
const qrAnalyzeBtn = byId('qr-analyze-btn');
const qrResetBtn = byId('qr-reset-btn');
const qrCameraBtn = byId('qr-camera-btn');
const cameraCard = byId('camera-card');
const cameraVideo = byId('camera-video');
const cameraCanvas = byId('camera-canvas');
const cameraCloseBtn = byId('camera-close-btn');
const cameraStatus = byId('camera-status');

// Trigger file picker
qrDropzone?.addEventListener('click', () => qrFileInput.click());
qrDropzone?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    qrFileInput.click();
  }
});

// Drag and drop handling
['dragenter', 'dragover'].forEach(name => {
  qrDropzone?.addEventListener(name, (e) => {
    e.preventDefault();
    qrDropzone.classList.add('dragover');
  });
});
['dragleave', 'drop'].forEach(name => {
  qrDropzone?.addEventListener(name, (e) => {
    e.preventDefault();
    qrDropzone.classList.remove('dragover');
  });
});

qrDropzone?.addEventListener('drop', (e) => {
  if (e.dataTransfer && e.dataTransfer.files.length > 0) {
    processQrFile(e.dataTransfer.files[0]);
  }
});

qrFileInput?.addEventListener('change', (e) => {
  if (e.target.files && e.target.files.length > 0) {
    processQrFile(e.target.files[0]);
  }
});

async function decodeWithBackend(fileOrBlob) {
  const formData = new FormData();
  formData.append('file', fileOrBlob);
  const data = await apiRequest('/api/qr/decode', {
    method: 'POST',
    body: formData,
  });
  return data.url;
}

function handleQrDecoded(decodedUrl) {
  activeQrUrl = decodedUrl.trim();
  qrDecodedUrl.textContent = activeQrUrl;
  qrResultBox.hidden = false;
  qrResultBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  const msg = byId('scan-message');
  if (msg) msg.hidden = true;
}

async function processQrFile(file) {
  if (!file) return;
  const message = byId('scan-message');
  message.hidden = true;
  qrResultBox.hidden = true;

  try {
    // 1. Try decoding with client-side jsQR via Canvas
    const img = new Image();
    const url = URL.createObjectURL(file);
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
      img.src = url;
    });

    let detectedText = null;
    const canvas = document.createElement('canvas');
    canvas.width = img.width;
    canvas.height = img.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);

    if (window.jsQR) {
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const code = window.jsQR(imageData.data, imageData.width, imageData.height);
      if (code && code.data) {
        detectedText = code.data;
      }
    }

    // 2. If jsQR didn't catch it, fallback to server-side OpenCV decoder
    if (!detectedText) {
      detectedText = await decodeWithBackend(file);
    }

    URL.revokeObjectURL(url);

    if (detectedText) {
      handleQrDecoded(detectedText);
    } else {
      throw new Error('No QR code could be detected in this image. Please use a clearer image.');
    }
  } catch (error) {
    setMessage(message, error.message || 'Could not decode QR code from the provided file.');
  }
}

// Camera Scanner Handling
async function startCamera() {
  const message = byId('scan-message');
  message.hidden = true;
  qrResultBox.hidden = true;

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setMessage(message, 'Camera access is not supported by your browser or environment.');
    return;
  }

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: 'environment', width: { ideal: 1280 }, height: { ideal: 720 } }
    });
    cameraVideo.srcObject = cameraStream;
    await cameraVideo.play();
    cameraCard.hidden = false;
    cameraStatus.textContent = 'Scanning frame for QR code...';
    startCameraScanningLoop();
  } catch (error) {
    setMessage(message, `Camera permission denied or camera unavailable: ${error.message}`);
    stopCamera();
  }
}

function stopCamera() {
  if (cameraScanLoopId) {
    cancelAnimationFrame(cameraScanLoopId);
    cameraScanLoopId = null;
  }
  if (cameraStream) {
    cameraStream.getTracks().forEach(track => track.stop());
    cameraStream = null;
  }
  if (cameraCard) cameraCard.hidden = true;
}

function startCameraScanningLoop() {
  const checkFrame = () => {
    if (!cameraStream) return;
    if (cameraVideo.readyState === cameraVideo.HAVE_ENOUGH_DATA) {
      cameraCanvas.width = cameraVideo.videoWidth;
      cameraCanvas.height = cameraVideo.videoHeight;
      const ctx = cameraCanvas.getContext('2d');
      ctx.drawImage(cameraVideo, 0, 0, cameraCanvas.width, cameraCanvas.height);

      if (window.jsQR) {
        const imageData = ctx.getImageData(0, 0, cameraCanvas.width, cameraCanvas.height);
        const code = window.jsQR(imageData.data, imageData.width, imageData.height);
        if (code && code.data && code.data.trim()) {
          stopCamera();
          handleQrDecoded(code.data);
          return;
        }
      }
    }
    cameraScanLoopId = requestAnimationFrame(checkFrame);
  };
  cameraScanLoopId = requestAnimationFrame(checkFrame);
}

qrCameraBtn?.addEventListener('click', startCamera);
cameraCloseBtn?.addEventListener('click', stopCamera);

// Analyze QR URL button
qrAnalyzeBtn?.addEventListener('click', async () => {
  if (!activeQrUrl) return;
  const message = byId('scan-message');
  message.hidden = true;
  qrAnalyzeBtn.disabled = true;
  qrAnalyzeBtn.querySelector('span:first-child').textContent = 'Analyzing QR URL...';

  try {
    const result = await apiRequest('/api/predict', {
      method: 'POST',
      body: JSON.stringify({ url: activeQrUrl, source: 'QR Code' }),
    });
    renderResult(result);
    byId('model-status').textContent = 'Trained model ready';
    byId('model-dot').classList.add('ready');
  } catch (error) {
    setMessage(message, error.message);
  } finally {
    qrAnalyzeBtn.disabled = false;
    qrAnalyzeBtn.querySelector('span:first-child').textContent = 'Analyze QR URL';
  }
});

qrResetBtn?.addEventListener('click', () => {
  activeQrUrl = null;
  qrResultBox.hidden = true;
  qrFileInput.value = '';
});

/* ==================================================
   STATUS & DASHBOARD & HISTORY
================================================== */
async function checkModelStatus() {
  const dot = byId('model-dot');
  const label = byId('model-status');
  try {
    const status = await apiRequest('/api/status');
    dot.classList.toggle('ready', status.model_ready);
    label.textContent = status.model_ready ? 'Trained model ready' : 'Training required';
    if (!status.model_ready) setMessage(byId('scan-message'), status.message);
  } catch {
    label.textContent = 'Server unavailable';
    setMessage(byId('scan-message'), 'Cannot connect to the Flask server. Check that it is running.');
  }
}

async function loadDashboard() {
  const message = byId('dashboard-message');
  message.hidden = true;
  try {
    const data = await apiRequest('/api/dashboard');
    byId('stat-total').textContent = data.total_scans;
    byId('stat-legitimate').textContent = data.legitimate_count;
    byId('stat-suspicious').textContent = data.suspicious_count;
    byId('stat-phishing').textContent = data.high_risk_count || data.phishing_count;
    byId('stat-qr').textContent = data.qr_scans_count || 0;
    byId('stat-avg').textContent = formatScore(data.average_risk_score);
    renderDistribution(data);
    renderRecent(data.recent_scans);
  } catch (error) {
    setMessage(message, error.message);
  }
}

function renderDistribution(data) {
  const chart = byId('distribution-chart');
  chart.replaceChildren();
  const categories = [
    ['Legitimate', data.legitimate_count, ''],
    ['Suspicious', data.suspicious_count, 'suspicious'],
    ['Phishing / High Risk', data.high_risk_count || data.phishing_count, 'phishing'],
  ];
  byId('chart-empty').hidden = data.total_scans > 0;
  chart.hidden = data.total_scans === 0;
  for (const [label, count, variant] of categories) {
    const row = makeElement('div', 'chart-row');
    const track = makeElement('div', 'chart-track');
    const fill = makeElement('div', `chart-fill ${variant}`);
    fill.style.width = `${data.total_scans ? (count / data.total_scans) * 100 : 0}%`;
    track.append(fill);
    row.append(makeElement('span', '', label), track, makeElement('span', 'chart-count', String(count)));
    chart.append(row);
  }
}

function renderRecent(scans) {
  const list = byId('recent-scans');
  list.replaceChildren();
  byId('recent-empty').hidden = scans.length > 0;
  for (const scan of scans) {
    const row = makeElement('div', 'recent-row');
    const urlSpan = makeElement('span', 'recent-url', scan.url);
    const sourceTag = makeElement('span', 'recent-source', scan.source || 'Manual');
    const timeSpan = makeElement('span', 'recent-time', formatDate(scan.scanned_at));
    const scoreSpan = makeElement('span', `recent-score score-${(scan.risk_level || 'low').toLowerCase()}`, `${formatScore(scan.risk_score)}`);
    row.append(urlSpan, sourceTag, timeSpan, scoreSpan);
    list.append(row);
  }
}

async function loadHistory() {
  const message = byId('history-message');
  message.hidden = true;
  try {
    const data = await apiRequest('/api/history');
    const body = byId('history-rows');
    body.replaceChildren();
    byId('history-empty').hidden = data.scans.length > 0;
    for (const scan of data.scans) {
      const row = document.createElement('tr');

      // URL
      const urlCell = document.createElement('td');
      urlCell.append(makeElement('span', 'history-url', scan.url));

      // Source
      const sourceCell = document.createElement('td');
      const isQr = (scan.source || '').toLowerCase().includes('qr');
      sourceCell.append(makeElement('span', `history-source-badge ${isQr ? 'qr' : 'manual'}`, scan.source || 'Manual'));

      // Classification
      const predictionCell = document.createElement('td');
      predictionCell.append(makeElement('span', `badge ${scan.prediction.toLowerCase()}`, scan.prediction));

      // Risk Score
      const scoreCell = document.createElement('td');
      scoreCell.append(makeElement('strong', 'history-score', `${formatScore(scan.risk_score)} / 100`));

      // Risk Level
      const levelCell = document.createElement('td');
      levelCell.append(makeElement('span', `risk-badge ${scan.risk_level.toLowerCase()}`, scan.risk_level.toUpperCase()));

      // Key Reasons
      const reasonsCell = document.createElement('td');
      const reasonsList = Array.isArray(scan.reasons) ? scan.reasons : [];
      if (reasonsList.length > 0) {
        const topReasons = reasonsList.slice(0, 2).join('; ');
        reasonsCell.append(makeElement('span', 'history-reasons', topReasons));
      } else {
        reasonsCell.append(makeElement('span', 'history-reasons muted', 'Baseline'));
      }

      // Timestamp
      const timeCell = document.createElement('td');
      timeCell.append(document.createTextNode(formatDate(scan.scanned_at)));

      row.append(urlCell, sourceCell, predictionCell, scoreCell, levelCell, reasonsCell, timeCell);
      body.append(row);
    }
  } catch (error) {
    setMessage(message, error.message);
  }
}

// Navigation View Events
for (const button of document.querySelectorAll('[data-view]')) {
  button.addEventListener('click', (event) => {
    event.preventDefault();
    setView(button.dataset.view);
  });
}
for (const button of document.querySelectorAll('[data-refresh]')) {
  button.addEventListener('click', () => button.dataset.refresh === 'dashboard' ? loadDashboard() : loadHistory());
}

// Clear History Dialog Events
const dialog = byId('confirm-dialog');
byId('clear-history')?.addEventListener('click', () => dialog.showModal());
byId('confirm-clear')?.addEventListener('click', async (event) => {
  event.preventDefault();
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await apiRequest('/api/history', { method: 'DELETE' });
    dialog.close();
    await loadHistory();
  } catch (error) {
    setMessage(byId('history-message'), error.message);
    dialog.close();
  } finally {
    button.disabled = false;
  }
});

checkModelStatus();
