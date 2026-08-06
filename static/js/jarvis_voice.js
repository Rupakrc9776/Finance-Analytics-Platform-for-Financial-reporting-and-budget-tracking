(function () {
    const micBtn = document.getElementById("jarvisMicBtn");
    const input = document.getElementById("jarvisInput");
    const panel = document.getElementById("jarvisPanel");
    const settingsToggle = document.getElementById("jarvisVoiceSettingsToggle");
    const settingsPanel = document.getElementById("jarvisVoiceSettings");
    const enabledCheckbox = document.getElementById("jarvisVoiceEnabled");
    const genderSelect = document.getElementById("jarvisVoiceGender");
    const rateInput = document.getElementById("jarvisVoiceRate");
    const pitchInput = document.getElementById("jarvisVoicePitch");
    const rateValue = document.getElementById("jarvisVoiceRateValue");
    const pitchValue = document.getElementById("jarvisVoicePitchValue");
    const voiceStatus = document.getElementById("jarvisVoiceStatus");
    const popupManager = window.jarvisPopupManager;

    if (!micBtn || !input || !enabledCheckbox || !genderSelect || !rateInput || !pitchInput || !voiceStatus) {
        return;
    }

    const storageKey = "sfi-jarvis-voice-v1";
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const hasSpeechRecognition = typeof SpeechRecognition !== "undefined";
    const hasSpeechSynthesis = typeof window.speechSynthesis !== "undefined";
    const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let recognition = null;
    let isListening = false;
    let availableVoices = [];
    let activeSpeechUtterance = null;
    let drawerTimer = null;

    const defaults = {
        enabled: true,
        gender: "auto",
        rate: 1,
        pitch: 1
    };

    function renderMicButton(listening) {
        if (listening) {
            micBtn.innerHTML = '<i class="fa-solid fa-stop"></i>Stop';
            micBtn.setAttribute("aria-label", "Stop voice input");
            micBtn.setAttribute("title", "Stop voice input");
            return;
        }
        micBtn.innerHTML = '<i class="fa-solid fa-microphone"></i>Mic';
        micBtn.setAttribute("aria-label", "Start voice input");
        micBtn.setAttribute("title", "Start voice input");
    }

    function loadSettings() {
        try {
            const raw = localStorage.getItem(storageKey);
            if (!raw) return Object.assign({}, defaults);
            const parsed = JSON.parse(raw);
            return {
                enabled: typeof parsed.enabled === "boolean" ? parsed.enabled : defaults.enabled,
                gender: ["auto", "male", "female"].includes(parsed.gender) ? parsed.gender : defaults.gender,
                rate: Number.isFinite(parsed.rate) ? parsed.rate : defaults.rate,
                pitch: Number.isFinite(parsed.pitch) ? parsed.pitch : defaults.pitch
            };
        } catch (_error) {
            return Object.assign({}, defaults);
        }
    }

    function saveSettings(settings) {
        localStorage.setItem(storageKey, JSON.stringify(settings));
    }

    function showStatus(message, tone) {
        voiceStatus.textContent = message || "";
        voiceStatus.dataset.tone = tone || "info";
    }

    const settings = loadSettings();
    enabledCheckbox.checked = settings.enabled;
    genderSelect.value = settings.gender;
    rateInput.value = String(settings.rate);
    pitchInput.value = String(settings.pitch);
    rateValue.textContent = Number(settings.rate).toFixed(1);
    pitchValue.textContent = Number(settings.pitch).toFixed(1);

    function persistSettings() {
        settings.enabled = !!enabledCheckbox.checked;
        settings.gender = genderSelect.value;
        settings.rate = Number(rateInput.value);
        settings.pitch = Number(pitchInput.value);
        rateValue.textContent = settings.rate.toFixed(1);
        pitchValue.textContent = settings.pitch.toFixed(1);
        saveSettings(settings);
    }

    enabledCheckbox.addEventListener("change", persistSettings);
    genderSelect.addEventListener("change", persistSettings);
    rateInput.addEventListener("input", persistSettings);
    pitchInput.addEventListener("input", persistSettings);

    function openSettingsDrawer() {
        if (!settingsPanel || !settingsToggle) {
            return;
        }
        if (drawerTimer) {
            clearTimeout(drawerTimer);
            drawerTimer = null;
        }
        settingsPanel.hidden = false;
        window.requestAnimationFrame(function () {
            settingsPanel.classList.add("is-open");
        });
        settingsToggle.setAttribute("aria-expanded", "true");
    }

    function closeSettingsDrawer() {
        if (!settingsPanel || !settingsToggle) {
            return;
        }
        settingsPanel.classList.remove("is-open");
        settingsToggle.setAttribute("aria-expanded", "false");
        drawerTimer = window.setTimeout(function () {
            settingsPanel.hidden = true;
            drawerTimer = null;
        }, prefersReducedMotion ? 0 : 220);
    }

    if (settingsToggle && settingsPanel && popupManager) {
        popupManager.registerPopup(settingsToggle, settingsPanel, {
            onOpen: openSettingsDrawer,
            onClose: closeSettingsDrawer,
        });

        document.addEventListener("jarvis:panel-closed", function () {
            closeSettingsDrawer();
        });
    }

    function pickVoice() {
        if (!availableVoices.length) {
            return null;
        }

        if (settings.gender === "female") {
            const female = availableVoices.find(function (voice) {
                return /female|woman|zira|susan|samantha|victoria|aria|jenny/i.test(voice.name);
            });
            if (female) return female;
        }

        if (settings.gender === "male") {
            const male = availableVoices.find(function (voice) {
                return /male|man|david|mark|guy|brian|adam|andrew/i.test(voice.name);
            });
            if (male) return male;
        }

        return availableVoices.find(function (voice) {
            return /en/i.test(voice.lang || "");
        }) || availableVoices[0];
    }

    function stopSpeaking() {
        if (!hasSpeechSynthesis) return;
        window.speechSynthesis.cancel();
        activeSpeechUtterance = null;
        document.querySelectorAll(".jarvis-speak-btn.is-speaking").forEach(function (button) {
            button.classList.remove("is-speaking");
        });
    }

    function speakText(text, speakerButton) {
        if (!settings.enabled || !hasSpeechSynthesis || !text) {
            return;
        }

        stopSpeaking();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = settings.rate;
        utterance.pitch = settings.pitch;
        const voice = pickVoice();
        if (voice) {
            utterance.voice = voice;
        }

        utterance.onstart = function () {
            if (speakerButton) {
                speakerButton.classList.add("is-speaking");
            }
            showStatus("Speaking...", "info");
        };

        utterance.onend = function () {
            if (speakerButton) {
                speakerButton.classList.remove("is-speaking");
            }
            activeSpeechUtterance = null;
            if (!isListening) {
                showStatus("", "info");
            }
        };

        utterance.onerror = function () {
            if (speakerButton) {
                speakerButton.classList.remove("is-speaking");
            }
            activeSpeechUtterance = null;
            showStatus("Unable to play voice output.", "error");
        };

        activeSpeechUtterance = utterance;
        window.speechSynthesis.speak(utterance);
    }

    function ensureSpeakerButton(bubble) {
        if (!bubble || !bubble.classList || !bubble.classList.contains("ai")) {
            return;
        }
        if (bubble.dataset.loading === "true") {
            return;
        }
        if (bubble.querySelector(".jarvis-speak-btn")) {
            return;
        }

        const text = bubble.dataset.rawText || bubble.textContent || "";
        if (!text.trim()) {
            return;
        }

        const button = document.createElement("button");
        button.type = "button";
        button.className = "jarvis-speak-btn";
        button.setAttribute("aria-label", "Replay voice response");
        button.setAttribute("title", "Replay voice");
        button.innerHTML = '<i class="fa-solid fa-volume-high"></i>';
        button.addEventListener("click", function () {
            speakText(text, button);
        });

        let actions = bubble.querySelector(".jarvis-msg-actions");
        if (!actions) {
            actions = document.createElement("div");
            actions.className = "jarvis-msg-actions";
            bubble.appendChild(actions);
        }

        actions.appendChild(button);

        if (window.jarvisChat && typeof window.jarvisChat.attachDeleteAction === "function") {
            window.jarvisChat.attachDeleteAction(bubble);
        }
    }

    function appendTranscript(text) {
        if (!text) return;
        const trimmed = text.trim();
        if (!trimmed) return;
        const current = input.value.trim();
        input.value = current ? current + " " + trimmed : trimmed;
        input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    function setListeningState(listening) {
        isListening = listening;
        micBtn.classList.toggle("is-listening", listening);
        micBtn.disabled = false;
        renderMicButton(listening);
        if (listening) {
            showStatus("Listening...", "listening");
        } else if (!activeSpeechUtterance) {
            showStatus("", "info");
        }
    }

    function stopListening() {
        if (recognition && isListening) {
            recognition.stop();
        }
    }

    function setupRecognition() {
        if (!hasSpeechRecognition) {
            micBtn.disabled = true;
            renderMicButton(false);
            showStatus("Voice input is not supported in this browser.", "warning");
            return;
        }

        recognition = new SpeechRecognition();
        recognition.lang = "en-US";
        recognition.continuous = false;
        recognition.interimResults = true;

        recognition.onstart = function () {
            setListeningState(true);
        };

        recognition.onresult = function (event) {
            let transcript = "";
            for (let i = event.resultIndex; i < event.results.length; i += 1) {
                transcript += event.results[i][0].transcript || "";
            }
            appendTranscript(transcript);
        };

        recognition.onerror = function (event) {
            if (event.error === "not-allowed" || event.error === "service-not-allowed") {
                showStatus("Microphone permission denied.", "error");
            } else if (event.error === "no-speech") {
                showStatus("No speech detected. Try again.", "warning");
            } else {
                showStatus("Voice input error: " + event.error, "error");
            }
        };

        recognition.onend = function () {
            setListeningState(false);
        };

        micBtn.addEventListener("click", function () {
            if (!settings.enabled) {
                showStatus("Enable Voice in settings to use microphone.", "warning");
                return;
            }
            if (isListening) {
                stopListening();
                return;
            }
            try {
                recognition.start();
            } catch (_error) {
                showStatus("Voice input is busy. Please try again.", "warning");
            }
        });
    }

    function loadVoices() {
        if (!hasSpeechSynthesis) {
            showStatus("Voice output is not supported in this browser.", "warning");
            return;
        }
        availableVoices = window.speechSynthesis.getVoices() || [];
    }

    if (hasSpeechSynthesis) {
        loadVoices();
        window.speechSynthesis.onvoiceschanged = loadVoices;
    }

    setupRecognition();
    renderMicButton(false);

    document.addEventListener("jarvis:message-added", function (event) {
        const detail = (event && event.detail) || {};
        const bubble = detail.bubble;
        ensureSpeakerButton(bubble);

        if (detail.role === "ai" && detail.speakable && settings.enabled) {
            const text = detail.text || (bubble ? (bubble.dataset.rawText || bubble.textContent || "") : "");
            const btn = bubble ? bubble.querySelector(".jarvis-speak-btn") : null;
            speakText(text, btn || null);
        }
    });

    document.addEventListener("jarvis:cleared", function () {
        stopSpeaking();
        if (!isListening) {
            showStatus("", "info");
        }
    });

    const existingAiBubbles = document.querySelectorAll(".jarvis-message.ai");
    existingAiBubbles.forEach(function (bubble) {
        ensureSpeakerButton(bubble);
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            stopListening();
            stopSpeaking();
        }
    });

    if (!hasSpeechRecognition && !hasSpeechSynthesis && window.jarvisChat && typeof window.jarvisChat.renderSystemMessage === "function") {
        window.jarvisChat.renderSystemMessage("Voice features are unavailable in this browser.");
    }
})();
