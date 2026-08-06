(function () {
    const panel = document.getElementById("jarvisPanel");
    if (!panel) {
        return;
    }

    const form = document.getElementById("jarvisForm");
    const input = document.getElementById("jarvisInput");
    const sendBtn = document.getElementById("jarvisSendBtn");
    const stopBtn = document.getElementById("jarvisStopGenBtn");
    const attachBtn = document.getElementById("jarvisAttachBtn");
    const fileInput = document.getElementById("jarvisFileInput");
    const attachmentList = document.getElementById("jarvisAttachmentList");

    const toolsToggle = document.getElementById("jarvisToolsToggle");
    const toolsMenu = document.getElementById("jarvisToolsMenu");
    const timelineBtn = document.getElementById("jarvisTimelineBtn");
    const clearBtn = document.getElementById("jarvisClearBtn");

    const aiStyle = document.getElementById("jarvisAiStyle");
    const language = document.getElementById("jarvisLanguage");
    const temperature = document.getElementById("jarvisTemperature");
    const temperatureValue = document.getElementById("jarvisTemperatureValue");
    const themePreference = document.getElementById("jarvisThemePreference");
    const voiceEnabled = document.getElementById("jarvisVoiceEnabled");
    const voiceGender = document.getElementById("jarvisVoiceGender");
    const speechRate = document.getElementById("jarvisVoiceRate");
    const pitch = document.getElementById("jarvisVoicePitch");

    const STATE = {
        currentThreadId: null,
        attachmentIds: [],
        isGenerating: false,
        abortController: null,
        settingsTimer: null,
    };

    function setGenerating(flag) {
        STATE.isGenerating = !!flag;
        if (sendBtn) {
            sendBtn.disabled = STATE.isGenerating;
        }
        if (stopBtn) {
            stopBtn.hidden = !STATE.isGenerating;
        }
    }

    function renderAttachmentPills(items) {
        if (!attachmentList) {
            return;
        }

        if (!Array.isArray(items) || items.length === 0) {
            attachmentList.hidden = true;
            attachmentList.innerHTML = "";
            return;
        }

        attachmentList.hidden = false;
        attachmentList.innerHTML = items.map(function (item) {
            const id = Number(item.id || 0);
            const name = String(item.name || "Attachment");
            return '<button type="button" class="jarvis-attachment-pill" data-attachment-id="' + id + '">' +
                '<i class="fa-solid fa-paperclip"></i>' +
                '<span>' + name + '</span>' +
                '<i class="fa-solid fa-xmark" aria-hidden="true"></i>' +
                '</button>';
        }).join("");
    }

    async function loadAttachments() {
        const response = await fetch("/api/jarvis/attachments?limit=12");
        if (!response.ok) {
            return [];
        }
        const payload = await response.json().catch(function () { return {}; });
        const list = Array.isArray(payload.attachments) ? payload.attachments : [];
        return list;
    }

    async function uploadAttachment(file) {
        const data = new FormData();
        data.append("file", file);

        const response = await fetch("/api/jarvis/attachments", {
            method: "POST",
            body: data,
        });

        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok) {
            throw new Error(payload.error || "Attachment upload failed.");
        }
        return payload.attachment || null;
    }

    async function removeAttachment(attachmentId) {
        const response = await fetch("/api/jarvis/attachments/" + encodeURIComponent(String(attachmentId)), {
            method: "DELETE"
        });
        return response.ok;
    }

    function applySettings(settings) {
        if (!settings || typeof settings !== "object") {
            return;
        }

        if (aiStyle) aiStyle.value = String(settings.response_style || "balanced");
        if (language) language.value = String(settings.language || "en");
        if (temperature) temperature.value = String(settings.temperature ?? 0.3);
        if (temperatureValue) temperatureValue.textContent = Number(settings.temperature ?? 0.3).toFixed(1);

        if (themePreference) themePreference.value = String(settings.theme_preference || "system");
        if (voiceEnabled) voiceEnabled.checked = !!settings.voice_enabled;
        if (voiceGender) voiceGender.value = String(settings.voice_gender || "auto");
        if (speechRate) speechRate.value = String(settings.speech_rate ?? 1);
        if (pitch) pitch.value = String(settings.pitch ?? 1);
    }

    async function fetchSettings() {
        const response = await fetch("/api/jarvis/settings");
        if (!response.ok) {
            return;
        }
        const payload = await response.json().catch(function () { return {}; });
        applySettings(payload);
    }

    async function saveSettings() {
        const payload = {
            response_style: aiStyle ? aiStyle.value : "balanced",
            language: language ? language.value : "en",
            temperature: temperature ? Number(temperature.value) : 0.3,
            theme_preference: themePreference ? themePreference.value : "system",
            voice_enabled: voiceEnabled ? !!voiceEnabled.checked : true,
            voice_gender: voiceGender ? voiceGender.value : "auto",
            speech_rate: speechRate ? Number(speechRate.value) : 1,
            pitch: pitch ? Number(pitch.value) : 1,
        };

        await fetch("/api/jarvis/settings", {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });
    }

    function queueSaveSettings() {
        if (STATE.settingsTimer) {
            clearTimeout(STATE.settingsTimer);
        }
        STATE.settingsTimer = window.setTimeout(function () {
            saveSettings().catch(function () { return; });
            STATE.settingsTimer = null;
        }, 220);
    }

    function collectThreadMessages(messages) {
        if (!window.jarvisChat || typeof window.jarvisChat.renderSystemMessage !== "function") {
            return;
        }

        const messagesEl = document.getElementById("jarvisMessages");
        if (!messagesEl) {
            return;
        }
        messagesEl.innerHTML = "";

        (messages || []).forEach(function (entry) {
            if (!entry) {
                return;
            }
            const q = String(entry.question || "");
            const a = String(entry.answer || "");
            if (q) {
                document.dispatchEvent(new CustomEvent("jarvis:v2-render-user", {
                    detail: {
                        text: q,
                        conversationId: entry.id,
                        createdAt: entry.created_at,
                    }
                }));
            }
            if (a) {
                document.dispatchEvent(new CustomEvent("jarvis:v2-render-ai", {
                    detail: {
                        text: a,
                        conversationId: entry.id,
                        createdAt: entry.created_at,
                        feedback: entry.feedback || "",
                    }
                }));
            }
        });
    }

    async function openThread(threadId) {
        const response = await fetch("/api/jarvis/history/threads/" + encodeURIComponent(String(threadId)) + "?limit=200");
        if (!response.ok) {
            return;
        }
        const payload = await response.json().catch(function () { return {}; });
        const items = Array.isArray(payload.messages) ? payload.messages : [];
        STATE.currentThreadId = String(threadId);
        collectThreadMessages(items);
    }

    async function loadThreads() {
        const response = await fetch("/api/jarvis/history/threads?limit=80");
        if (!response.ok) {
            return [];
        }
        const payload = await response.json().catch(function () { return {}; });
        return Array.isArray(payload.threads) ? payload.threads : [];
    }

    function renderThreadsInMenu(threads) {
        if (!toolsMenu) {
            return;
        }

        const old = toolsMenu.querySelector(".jarvis-thread-list");
        if (old) {
            old.remove();
        }

        const wrap = document.createElement("div");
        wrap.className = "jarvis-thread-list";
        if (!threads.length) {
            wrap.innerHTML = '<div class="jarvis-thread-empty">No conversation history yet.</div>';
            toolsMenu.appendChild(wrap);
            return;
        }

        wrap.innerHTML = threads.slice(0, 12).map(function (thread) {
            const threadId = String(thread.thread_id || "");
            const title = String(thread.title || "Conversation");
            return '<div class="jarvis-thread-item" data-thread-id="' + threadId + '">' +
                '<button type="button" class="jarvis-thread-open" title="Open conversation">' + title + '</button>' +
                '<button type="button" class="jarvis-thread-rename" title="Rename conversation"><i class="fa-solid fa-pen"></i></button>' +
                '<button type="button" class="jarvis-thread-delete" title="Delete conversation"><i class="fa-solid fa-trash"></i></button>' +
                '</div>';
        }).join("");

        toolsMenu.appendChild(wrap);
    }

    async function refreshThreads() {
        const threads = await loadThreads();
        renderThreadsInMenu(threads);
    }

    function bindSettingsEvents() {
        [aiStyle, language, temperature, themePreference, voiceEnabled, voiceGender, speechRate, pitch]
            .filter(Boolean)
            .forEach(function (node) {
                node.addEventListener("change", queueSaveSettings);
                node.addEventListener("input", queueSaveSettings);
            });

        if (temperature && temperatureValue) {
            temperature.addEventListener("input", function () {
                temperatureValue.textContent = Number(temperature.value).toFixed(1);
            });
        }
    }

    function bindAttachmentEvents() {
        if (attachBtn && fileInput) {
            attachBtn.addEventListener("click", function () {
                fileInput.click();
            });
        }

        if (fileInput) {
            fileInput.addEventListener("change", async function () {
                const file = fileInput.files && fileInput.files[0];
                if (!file) {
                    return;
                }
                try {
                    const uploaded = await uploadAttachment(file);
                    if (uploaded && uploaded.id) {
                        if (!STATE.attachmentIds.includes(Number(uploaded.id))) {
                            STATE.attachmentIds.push(Number(uploaded.id));
                        }
                    }
                    const list = await loadAttachments();
                    renderAttachmentPills(list.filter(function (item) {
                        return STATE.attachmentIds.includes(Number(item.id));
                    }));
                    if (window.jarvisChat && typeof window.jarvisChat.renderSystemMessage === "function") {
                        window.jarvisChat.renderSystemMessage("Attachment uploaded: " + String(uploaded.name || "file") + ".");
                    }
                } catch (error) {
                    if (window.jarvisChat && typeof window.jarvisChat.renderSystemMessage === "function") {
                        window.jarvisChat.renderSystemMessage(error && error.message ? error.message : "Attachment upload failed.");
                    }
                } finally {
                    fileInput.value = "";
                }
            });
        }

        if (attachmentList) {
            attachmentList.addEventListener("click", async function (event) {
                const target = event.target;
                const pill = target && target.closest ? target.closest(".jarvis-attachment-pill") : null;
                if (!pill) {
                    return;
                }
                const attachmentId = Number(pill.getAttribute("data-attachment-id") || "0");
                if (!attachmentId) {
                    return;
                }
                const ok = await removeAttachment(attachmentId);
                if (ok) {
                    STATE.attachmentIds = STATE.attachmentIds.filter(function (x) {
                        return Number(x) !== attachmentId;
                    });
                    const list = await loadAttachments();
                    renderAttachmentPills(list.filter(function (item) {
                        return STATE.attachmentIds.includes(Number(item.id));
                    }));
                }
            });
        }
    }

    function bindHistoryEvents() {
        if (timelineBtn) {
            timelineBtn.addEventListener("click", function () {
                refreshThreads().catch(function () { return; });
            });
        }

        if (toolsMenu) {
            toolsMenu.addEventListener("click", async function (event) {
                const target = event.target;
                const item = target && target.closest ? target.closest(".jarvis-thread-item") : null;
                if (!item) {
                    return;
                }
                const threadId = String(item.getAttribute("data-thread-id") || "");
                if (!threadId) {
                    return;
                }

                if (target.closest(".jarvis-thread-open")) {
                    openThread(threadId).catch(function () { return; });
                    return;
                }

                if (target.closest(".jarvis-thread-rename")) {
                    const currentTitle = item.querySelector(".jarvis-thread-open") ? item.querySelector(".jarvis-thread-open").textContent : "Conversation";
                    const nextTitle = window.prompt("Rename conversation", currentTitle || "Conversation");
                    if (!nextTitle || !nextTitle.trim()) {
                        return;
                    }
                    await fetch("/api/jarvis/history/threads/" + encodeURIComponent(threadId), {
                        method: "PATCH",
                        headers: {
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({ title: nextTitle.trim() }),
                    });
                    refreshThreads().catch(function () { return; });
                    return;
                }

                if (target.closest(".jarvis-thread-delete")) {
                    const confirmed = window.confirm("Delete this conversation?");
                    if (!confirmed) {
                        return;
                    }
                    await fetch("/api/jarvis/history/threads/" + encodeURIComponent(threadId), {
                        method: "DELETE",
                    });
                    if (STATE.currentThreadId === threadId) {
                        STATE.currentThreadId = null;
                    }
                    refreshThreads().catch(function () { return; });
                }
            });
        }

        if (clearBtn) {
            clearBtn.addEventListener("click", function () {
                STATE.currentThreadId = null;
            });
        }
    }

    function bindStreamingProxy() {
        if (!form || !input) {
            return;
        }

        form.addEventListener("submit", function (event) {
            if (event.defaultPrevented) {
                return;
            }

            if (STATE.isGenerating) {
                event.preventDefault();
                return;
            }

            const message = (input.value || "").trim();
            if (!message) {
                return;
            }

            event.preventDefault();

            const userBubble = document.createElement("div");
            userBubble.className = "jarvis-message user";
            userBubble.innerHTML = '<div class="jarvis-user-content"></div><div class="jarvis-msg-time"></div>';
            userBubble.querySelector(".jarvis-user-content").textContent = message;
            userBubble.querySelector(".jarvis-msg-time").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
            const messagesEl = document.getElementById("jarvisMessages");
            if (messagesEl) {
                const welcome = messagesEl.querySelector(".jarvis-welcome");
                if (welcome) {
                    welcome.remove();
                }
                messagesEl.appendChild(userBubble);
                messagesEl.scrollTop = messagesEl.scrollHeight;
            }

            input.value = "";
            input.dispatchEvent(new Event("input", { bubbles: true }));

            STATE.abortController = new AbortController();
            setGenerating(true);

            fetch("/api/jarvis/chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    message: message,
                    thread_id: STATE.currentThreadId,
                    attachment_ids: STATE.attachmentIds,
                    conversation_title: message.slice(0, 72),
                }),
                signal: STATE.abortController.signal,
            }).then(function (response) {
                return response.json().catch(function () { return {}; }).then(function (payload) {
                    return { ok: response.ok, payload: payload };
                });
            }).then(function (result) {
                const payload = result.payload || {};
                const messages = document.getElementById("jarvisMessages");
                if (!messages) {
                    return;
                }
                const ai = document.createElement("div");
                ai.className = "jarvis-message ai";
                ai.innerHTML = '<div class="jarvis-ai-head"><span class="jarvis-ai-avatar"><i class="fa-solid fa-robot"></i></span><strong>JARVIS</strong></div>' +
                    '<div class="jarvis-ai-content"></div><div class="jarvis-msg-time"></div>';
                ai.querySelector(".jarvis-ai-content").textContent = payload.reply || payload.error || "No response.";
                ai.querySelector(".jarvis-msg-time").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
                messages.appendChild(ai);
                messages.scrollTop = messages.scrollHeight;

                if (payload.thread_id) {
                    STATE.currentThreadId = String(payload.thread_id);
                } else if (!STATE.currentThreadId && payload.history_id) {
                    STATE.currentThreadId = "legacy-" + String(payload.history_id);
                }

                refreshThreads().catch(function () { return; });
            }).catch(function (error) {
                if (error && error.name === "AbortError") {
                    return;
                }
                if (window.jarvisChat && typeof window.jarvisChat.renderSystemMessage === "function") {
                    window.jarvisChat.renderSystemMessage("Unable to get assistant response right now.");
                }
            }).finally(function () {
                setGenerating(false);
                STATE.abortController = null;
                if (input) {
                    input.focus();
                }
            });
        }, true);

        if (stopBtn) {
            stopBtn.addEventListener("click", function () {
                if (STATE.abortController) {
                    STATE.abortController.abort();
                }
                setGenerating(false);
            });
        }
    }

    function init() {
        bindSettingsEvents();
        bindAttachmentEvents();
        bindHistoryEvents();
        bindStreamingProxy();

        fetchSettings().catch(function () { return; });
        refreshThreads().catch(function () { return; });
        loadAttachments().then(function (items) {
            renderAttachmentPills(items.filter(function (item) {
                return STATE.attachmentIds.includes(Number(item.id));
            }));
        }).catch(function () { return; });
    }

    init();
})();
