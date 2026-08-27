(function () {
    const fab = document.getElementById("jarvisFab");
    const panel = document.getElementById("jarvisPanel");
    const closeBtn = document.getElementById("jarvisCloseBtn");
    const clearBtn = document.getElementById("jarvisClearBtn");
    const form = document.getElementById("jarvisForm");
    const input = document.getElementById("jarvisInput");
    const sendBtn = document.getElementById("jarvisSendBtn");
    const messagesEl = document.getElementById("jarvisMessages");
    const clearMemoryBtn = document.getElementById("jarvisClearMemoryBtn");
    const searchToggle = document.getElementById("jarvisSearchToggle");
    const searchCard = document.getElementById("jarvisSearchCard");
    const toolsToggle = document.getElementById("jarvisToolsToggle");
    const toolsMenu = document.getElementById("jarvisToolsMenu");
    const exportBtn = document.getElementById("jarvisExportBtn");
    const timelineBtn = document.getElementById("jarvisTimelineBtn");
    let timelineEl = document.getElementById("jarvisTimeline");
    const searchInput = document.getElementById("jarvisSearchInput");
    const searchBtn = document.getElementById("jarvisSearchBtn");
    const suggestionsToggle = document.getElementById("jarvisSuggestionsToggle");
    const suggestionsBody = document.getElementById("jarvisSuggestionsBody");
    const suggestionsEl = document.getElementById("jarvisSuggestions");
    const generateReportBtn = document.getElementById("jarvisGenerateReportBtn");
    const downloadReportBtn = document.getElementById("jarvisDownloadReportBtn");
    const emailReportBtn = document.getElementById("jarvisEmailReportBtn");
    const popupManager = window.jarvisPopupManager;
    const prefersReducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (!fab || !panel || !form || !input || !sendBtn || !messagesEl) {
        return;
    }

    let isWaiting = false;
    let closeTimer = null;
    let searchCloseTimer = null;

    function openPanel() {
        if (closeTimer) {
            clearTimeout(closeTimer);
            closeTimer = null;
        }
        panel.hidden = false;
        window.requestAnimationFrame(function () {
            panel.classList.add("is-open");
        });
        fab.setAttribute("aria-expanded", "true");
        document.body.classList.add("jarvis-open");
        input.focus();
        scrollToBottom();
    }

    function closePanel() {
        panel.classList.remove("is-open");
        fab.setAttribute("aria-expanded", "false");
        document.body.classList.remove("jarvis-open");
        closeSearchCard();
        closeToolsMenu();
        document.dispatchEvent(new CustomEvent("jarvis:panel-closed"));
        closeTimer = window.setTimeout(function () {
            panel.hidden = true;
            closeTimer = null;
        }, prefersReducedMotion ? 0 : 220);
    }

    function togglePanel() {
        if (panel.hidden) {
            openPanel();
            return;
        }
        closePanel();
    }

    function autoResizeInput() {
        input.style.height = "auto";
        const next = Math.max(42, Math.min(input.scrollHeight, 140));
        input.style.height = next + "px";
    }

    function toggleSuggestions(forceExpand) {
        if (!suggestionsToggle || !suggestionsBody) {
            return;
        }
        const shouldExpand = typeof forceExpand === "boolean" ? forceExpand : suggestionsBody.hidden;
        suggestionsBody.hidden = !shouldExpand;
        suggestionsToggle.setAttribute("aria-expanded", shouldExpand ? "true" : "false");
    }

    if (suggestionsEl) {
        suggestionsEl.addEventListener("click", function (event) {
            const chip = event.target.closest(".jarvis-chip");
            if (!chip || !suggestionsEl.contains(chip) || isWaiting) {
                return;
            }

            event.preventDefault();
            input.value = chip.dataset.prompt || chip.textContent.trim();
            autoResizeInput();
            input.focus();
        });
    }

    function ensureTimelineEl() {
        if (timelineEl) {
            return timelineEl;
        }
        const panelBody = panel.querySelector(".jarvis-panel-body");
        if (!panelBody) {
            return null;
        }

        timelineEl = document.createElement("div");
        timelineEl.id = "jarvisTimeline";
        timelineEl.className = "jarvis-timeline";
        timelineEl.hidden = true;

        const messages = document.getElementById("jarvisMessages");
        if (messages) {
            panelBody.insertBefore(timelineEl, messages);
        } else {
            panelBody.appendChild(timelineEl);
        }

        return timelineEl;
    }

    function hasConversationMessages() {
        return !!messagesEl.querySelector(".jarvis-message");
    }

    function syncSuggestionsState() {
        if (!suggestionsToggle || !suggestionsBody) {
            return;
        }
        const hasMessages = hasConversationMessages();
        if (hasMessages) {
            toggleSuggestions(false);
        }
    }

    function closeSearchCard() {
        if (!searchCard || !searchToggle) {
            return;
        }
        if (searchCloseTimer) {
            clearTimeout(searchCloseTimer);
            searchCloseTimer = null;
        }
        searchCard.classList.remove("is-open");
        searchToggle.setAttribute("aria-expanded", "false");
        searchToggle.classList.remove("is-active");
        searchCloseTimer = window.setTimeout(function () {
            searchCard.hidden = true;
            searchCloseTimer = null;
        }, prefersReducedMotion ? 0 : 180);
    }

    function openSearchCard() {
        if (!searchCard || !searchToggle) {
            return;
        }
        if (popupManager && typeof popupManager.closeAllPopups === "function") {
            popupManager.closeAllPopups();
        }
        if (searchCloseTimer) {
            clearTimeout(searchCloseTimer);
            searchCloseTimer = null;
        }
        searchCard.hidden = false;
        window.requestAnimationFrame(function () {
            searchCard.classList.add("is-open");
        });
        searchToggle.setAttribute("aria-expanded", "true");
        searchToggle.classList.add("is-active");
        if (searchInput) {
            searchInput.focus();
        }
    }

    function toggleSearchCard() {
        if (!searchCard) {
            return;
        }
        if (searchCard.hidden) {
            openSearchCard();
            return;
        }
        closeSearchCard();
    }

    function closeToolsMenu() {
        if (!toolsMenu || !toolsToggle) {
            return;
        }
        toolsMenu.classList.remove("is-open");
        toolsToggle.setAttribute("aria-expanded", "false");
        toolsToggle.classList.remove("is-active");
        window.setTimeout(function () {
            toolsMenu.hidden = true;
        }, prefersReducedMotion ? 0 : 190);
    }

    function openToolsMenu() {
        if (!toolsMenu || !toolsToggle) {
            return;
        }
        if (popupManager && typeof popupManager.closeAllPopups === "function") {
            popupManager.closeAllPopups();
        }
        toolsMenu.hidden = false;
        window.requestAnimationFrame(function () {
            toolsMenu.classList.add("is-open");
        });
        toolsToggle.setAttribute("aria-expanded", "true");
        toolsToggle.classList.add("is-active");
        refreshThreads().catch(function () { return; });
    }

    function toggleToolsMenu() {
        if (!toolsMenu || !toolsToggle) {
            return;
        }
        if (toolsMenu.hidden) {
            openToolsMenu();
            return;
        }
        closeToolsMenu();
    }

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function clearWelcomeScreen() {
        const welcome = messagesEl.querySelector(".jarvis-welcome");
        if (welcome) {
            welcome.remove();
        }
    }

    function renderWelcomeScreen() {
        messagesEl.innerHTML = "";
        const welcome = document.createElement("section");
        welcome.className = "jarvis-welcome";
        welcome.innerHTML = [
            '<h3>Welcome to Finance Analytics Platform for Financial Reporting and Budget Tracking AI Assistant</h3>',
            '<p>Ask about spending, savings, goals, investments, and reports. I will answer using your finance data.</p>',
            '<div class="jarvis-welcome-pills">',
            '<span>Budget analysis</span>',
            '<span>Expense insights</span>',
            '<span>Investment guidance</span>',
            '<span>Goal tracking</span>',
            '</div>'
        ].join("");
        messagesEl.appendChild(welcome);
        if (suggestionsToggle) {
            suggestionsToggle.hidden = false;
        }
        if (suggestionsBody) {
            toggleSuggestions(false);
        }
        scrollToBottom();
    }

    function setBusy(busy) {
        isWaiting = busy;
        input.disabled = busy;
        sendBtn.disabled = busy;
        if (generateReportBtn) {
            generateReportBtn.disabled = !!busy;
        }
        if (emailReportBtn) {
            emailReportBtn.disabled = !!busy;
        }
        if (suggestionsEl) {
            suggestionsEl.querySelectorAll(".jarvis-chip").forEach(function (chip) {
                chip.disabled = !!busy;
            });
        }
    }

    function escapeHtml(raw) {
        return String(raw)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function getTimestampLabel(value) {
        const date = value ? new Date(value) : new Date();
        if (Number.isNaN(date.getTime())) {
            return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        }
        return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function appendTimestamp(bubble, value) {
        if (!bubble) {
            return;
        }
        const stamp = document.createElement("div");
        stamp.className = "jarvis-msg-time";
        stamp.textContent = getTimestampLabel(value);
        bubble.appendChild(stamp);
    }

    function splitTableRow(rowLine) {
        const trimmed = rowLine.trim().replace(/^\|/, "").replace(/\|$/, "");
        return trimmed.split("|").map(function (cell) {
            return cell.trim();
        });
    }

    function renderMarkdownTables(text) {
        const lines = text.split("\n");
        const rendered = [];
        let index = 0;

        while (index < lines.length) {
            const head = lines[index];
            const divider = lines[index + 1] || "";

            const isTableHeader = head.indexOf("|") !== -1;
            const isTableDivider = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$/.test(divider);

            if (!isTableHeader || !isTableDivider) {
                rendered.push(head);
                index += 1;
                continue;
            }

            const headerCells = splitTableRow(head);
            const bodyRows = [];
            index += 2;
            while (index < lines.length && lines[index].indexOf("|") !== -1 && lines[index].trim()) {
                bodyRows.push(splitTableRow(lines[index]));
                index += 1;
            }

            let tableHtml = '<div class="jarvis-table-wrap"><table class="jarvis-markdown-table"><thead><tr>';
            headerCells.forEach(function (cell) {
                tableHtml += "<th>" + cell + "</th>";
            });
            tableHtml += "</tr></thead><tbody>";

            bodyRows.forEach(function (row) {
                tableHtml += "<tr>";
                for (let i = 0; i < headerCells.length; i += 1) {
                    tableHtml += "<td>" + (row[i] || "") + "</td>";
                }
                tableHtml += "</tr>";
            });

            tableHtml += "</tbody></table></div>";
            rendered.push(tableHtml);
        }

        return rendered.join("\n");
    }

    function markdownToRich(markdown) {
        const chartBlocks = [];
        const codeBlocks = [];

        const withPlaceholders = String(markdown).replace(/```([^\n`]*)\n?([\s\S]*?)```/g, function (_full, langRaw, blockRaw) {
            const lang = String(langRaw || "").trim().toLowerCase();
            const body = String(blockRaw || "").trim();
            if (lang === "chart") {
                const chartIdx = chartBlocks.push(body) - 1;
                return "@@CHARTBLOCK" + chartIdx + "@@";
            }

            const codeIdx = codeBlocks.push({
                lang: lang || "text",
                body: body,
            }) - 1;
            return "@@CODEBLOCK" + codeIdx + "@@";
        });

        let html = escapeHtml(withPlaceholders);
        html = renderMarkdownTables(html);
        html = html
            .replace(/^###\s+(.*)$/gm, "<h4>$1</h4>")
            .replace(/^##\s+(.*)$/gm, "<h3>$1</h3>")
            .replace(/^#\s+(.*)$/gm, "<h2>$1</h2>")
            .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
            .replace(/\*(.*?)\*/g, "<em>$1</em>")
            .replace(/`([^`]+)`/g, "<code>$1</code>")
            .replace(/\[(.*?)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

        html = html.replace(/^(?:-\s+.*(?:\n|$))+?/gm, function (listBlock) {
            const items = listBlock
                .trim()
                .split(/\n/)
                .map(function (line) {
                    return "<li>" + line.replace(/^-\s+/, "") + "</li>";
                })
                .join("");
            return "<ul>" + items + "</ul>";
        });

        html = html
            .split(/\n{2,}/)
            .map(function (chunk) {
                if (/^<h[234]>|^<ul>|^<pre>|^<div class=\"jarvis-table-wrap\"/.test(chunk.trim())) {
                    return chunk;
                }
                return "<p>" + chunk.replace(/\n/g, "<br>") + "</p>";
            })
            .join("");

        html = html.replace(/@@CODEBLOCK(\d+)@@/g, function (_token, idxRaw) {
            const idx = Number(idxRaw);
            const code = codeBlocks[idx];
            if (!code) {
                return "";
            }
            return '<pre><span class="jarvis-code-lang">' + escapeHtml(code.lang) + '</span><code>' + escapeHtml(code.body) + "</code></pre>";
        });

        html = html.replace(/@@CHARTBLOCK(\d+)@@/g, function (_token, idxRaw) {
            return '<div class="jarvis-chart" data-chart-index="' + Number(idxRaw) + '"></div>';
        });

        return {
            html: html,
            chartBlocks: chartBlocks,
        };
    }

    function renderEmbeddedCharts(bubble, chartBlocks) {
        if (!bubble || !Array.isArray(chartBlocks) || chartBlocks.length === 0) {
            return;
        }

        const placeholders = bubble.querySelectorAll(".jarvis-chart[data-chart-index]");
        placeholders.forEach(function (holder) {
            const index = Number(holder.getAttribute("data-chart-index") || "-1");
            const raw = chartBlocks[index];
            if (index < 0 || !raw) {
                return;
            }

            let parsed;
            try {
                parsed = JSON.parse(raw);
            } catch (_error) {
                holder.innerHTML = '<small>Invalid chart payload.</small>';
                return;
            }

            if (typeof window.Chart === "undefined") {
                holder.innerHTML = '<small>Chart renderer unavailable.</small>';
                return;
            }

            const canvas = document.createElement("canvas");
            canvas.height = 200;
            holder.innerHTML = "";
            holder.appendChild(canvas);

            const chartType = parsed.type || "bar";
            const chartData = parsed.data || {
                labels: parsed.labels || [],
                datasets: parsed.datasets || [],
            };

            try {
                new window.Chart(canvas, {
                    type: chartType,
                    data: chartData,
                    options: Object.assign({
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: prefersReducedMotion ? false : { duration: 420 },
                        plugins: {
                            legend: {
                                display: true,
                                labels: { boxWidth: 10 }
                            }
                        }
                    }, parsed.options || {}),
                });
            } catch (_error) {
                holder.innerHTML = '<small>Unable to render chart.</small>';
            }
        });
    }

    function ensureActionRow(bubble) {
        let actions = bubble.querySelector(".jarvis-msg-actions");
        if (!actions) {
            actions = document.createElement("div");
            actions.className = "jarvis-msg-actions";
            bubble.appendChild(actions);
        }
        return actions;
    }

    function setFeedbackVisualState(bubble, feedback) {
        const likeBtn = bubble.querySelector(".jarvis-like-btn");
        const dislikeBtn = bubble.querySelector(".jarvis-dislike-btn");
        if (likeBtn) {
            likeBtn.classList.toggle("is-active", feedback === "like");
        }
        if (dislikeBtn) {
            dislikeBtn.classList.toggle("is-active", feedback === "dislike");
        }
        if (feedback) {
            bubble.dataset.feedback = feedback;
        } else {
            delete bubble.dataset.feedback;
        }
    }

    async function setConversationFeedback(conversationId, feedback) {
        const response = await fetch("/api/jarvis/history/" + encodeURIComponent(String(conversationId)) + "/feedback", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({ feedback: feedback })
        });
        if (!response.ok) {
            throw new Error("Unable to update feedback.");
        }
        return response.json();
    }

    function getConversationUserPrompt(conversationId, fallbackBubble) {
        if (conversationId) {
            const node = messagesEl.querySelector('.jarvis-message.user[data-conversation-id="' + conversationId + '"]');
            if (node && node.dataset.rawText) {
                return node.dataset.rawText;
            }
        }

        let current = fallbackBubble ? fallbackBubble.previousElementSibling : null;
        while (current) {
            if (current.classList.contains("jarvis-message") && current.classList.contains("user")) {
                return current.dataset.rawText || current.textContent || "";
            }
            current = current.previousElementSibling;
        }
        return "";
    }

    function ensureUtilityActions(bubble) {
        if (!bubble || !bubble.classList.contains("ai") || bubble.dataset.loading === "true") {
            return;
        }

        const actions = ensureActionRow(bubble);
        const conversationId = bubble.dataset.conversationId || "";

        if (!actions.querySelector(".jarvis-copy-btn")) {
            const copyBtn = document.createElement("button");
            copyBtn.type = "button";
            copyBtn.className = "jarvis-copy-btn";
            copyBtn.setAttribute("aria-label", "Copy response");
            copyBtn.setAttribute("title", "Copy response");
            copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>';
            actions.appendChild(copyBtn);
        }

        if (!actions.querySelector(".jarvis-regenerate-btn")) {
            const regenerateBtn = document.createElement("button");
            regenerateBtn.type = "button";
            regenerateBtn.className = "jarvis-regenerate-btn";
            regenerateBtn.setAttribute("aria-label", "Regenerate response");
            regenerateBtn.setAttribute("title", "Regenerate response");
            regenerateBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i>';
            actions.appendChild(regenerateBtn);
        }

        if (conversationId && !actions.querySelector(".jarvis-like-btn")) {
            const likeBtn = document.createElement("button");
            likeBtn.type = "button";
            likeBtn.className = "jarvis-like-btn";
            likeBtn.setAttribute("aria-label", "Like response");
            likeBtn.setAttribute("title", "Like response");
            likeBtn.innerHTML = '<i class="fa-regular fa-thumbs-up"></i>';
            actions.appendChild(likeBtn);
        }

        if (conversationId && !actions.querySelector(".jarvis-dislike-btn")) {
            const dislikeBtn = document.createElement("button");
            dislikeBtn.type = "button";
            dislikeBtn.className = "jarvis-dislike-btn";
            dislikeBtn.setAttribute("aria-label", "Dislike response");
            dislikeBtn.setAttribute("title", "Dislike response");
            dislikeBtn.innerHTML = '<i class="fa-regular fa-thumbs-down"></i>';
            actions.appendChild(dislikeBtn);
        }

        if (conversationId && !actions.querySelector(".jarvis-delete-btn")) {
            const deleteBtn = document.createElement("button");
            deleteBtn.type = "button";
            deleteBtn.className = "jarvis-delete-btn";
            deleteBtn.setAttribute("aria-label", "Delete conversation");
            deleteBtn.setAttribute("title", "Delete conversation");
            deleteBtn.setAttribute("data-conversation-id", String(conversationId));
            deleteBtn.innerHTML = '<i class="fa-solid fa-trash"></i>';
            actions.appendChild(deleteBtn);
        }
    }

    function typewriterReveal(bubble, rawText, finalHtml, chartBlocks) {
        if (prefersReducedMotion) {
            bubble.innerHTML = finalHtml;
            renderEmbeddedCharts(bubble, chartBlocks);
            ensureUtilityActions(bubble);
            return;
        }

        const limited = String(rawText || "").slice(0, 900);
        let cursor = 0;
        const step = function () {
            cursor += Math.max(2, Math.floor(limited.length / 60));
            if (cursor >= limited.length) {
                bubble.innerHTML = finalHtml;
                renderEmbeddedCharts(bubble, chartBlocks);
                ensureUtilityActions(bubble);
                return;
            }
            bubble.innerHTML = '<p class="jarvis-typing-reveal">' + escapeHtml(limited.slice(0, cursor)) + '<span class="jarvis-caret"></span></p>';
            window.requestAnimationFrame(step);
        };
        window.requestAnimationFrame(step);
    }

    function renderMessage(role, text, isLoading, options) {
        const meta = options || {};
        clearWelcomeScreen();
        const bubble = document.createElement("div");
        bubble.className = "jarvis-message " + role;
        bubble.dataset.role = role;

        if (isLoading) {
            bubble.innerHTML = '<div class="jarvis-loading" aria-label="Jarvis is typing"><span></span><span></span><span></span></div>';
            bubble.dataset.loading = "true";
            messagesEl.appendChild(bubble);
            scrollToBottom();
            return bubble;
        }

        bubble.dataset.loading = "false";
        bubble.dataset.rawText = text;
        bubble.dataset.speakable = meta.speakable ? "true" : "false";
        if (meta.conversationId) {
            bubble.dataset.conversationId = String(meta.conversationId);
        }

        if (role === "ai") {
            const parsed = markdownToRich(text);
            const head = '<div class="jarvis-ai-head"><span class="jarvis-ai-avatar"><i class="fa-solid fa-robot"></i></span><strong>JARVIS</strong></div>';
            const content = '<div class="jarvis-ai-content">' + parsed.html + "</div>";

            if (meta.animate) {
                bubble.innerHTML = head + '<div class="jarvis-ai-content"></div>';
                const contentNode = bubble.querySelector(".jarvis-ai-content");
                typewriterReveal(contentNode, text, parsed.html, parsed.chartBlocks);
            } else {
                bubble.innerHTML = head + content;
                const contentNode = bubble.querySelector(".jarvis-ai-content") || bubble;
                renderEmbeddedCharts(contentNode, parsed.chartBlocks);
            }

            ensureUtilityActions(bubble);
            if (meta.feedback) {
                setFeedbackVisualState(bubble, String(meta.feedback));
            }
            appendTimestamp(bubble, meta.timestamp);
        } else {
            bubble.innerHTML = '<div class="jarvis-user-content">' + escapeHtml(text) + "</div>";
            appendTimestamp(bubble, meta.timestamp);
        }

        messagesEl.appendChild(bubble);
        if (role === "user") {
            syncSuggestionsState();
        }
        if (!meta.deferScroll) {
            scrollToBottom();
        }

        document.dispatchEvent(new CustomEvent("jarvis:message-added", {
            detail: {
                role: role,
                text: text,
                bubble: bubble,
                speakable: !!meta.speakable,
                source: meta.source || "chat"
            }
        }));

        return bubble;
    }

    async function fetchHistory(query) {
        const q = (query || "").trim();
        const url = q ? "/api/jarvis/history?q=" + encodeURIComponent(q) + "&limit=100" : "/api/jarvis/history?limit=50";
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error("Unable to load conversation history.");
        }
        const payload = await response.json();
        return Array.isArray(payload.conversations) ? payload.conversations : [];
    }

    async function renderHistory(query) {
        try {
            const records = await fetchHistory(query);
            messagesEl.innerHTML = "";

            if (records.length === 0) {
                renderWelcomeScreen();
                return;
            }

            toggleSuggestions(false);

            const chronological = records.slice().reverse();
            let index = 0;
            const chunkSize = 8;

            const drawNextChunk = function () {
                const limit = Math.min(index + chunkSize, chronological.length);
                for (; index < limit; index += 1) {
                    const entry = chronological[index];
                    renderMessage("user", entry.question || "", false, {
                        speakable: false,
                        source: "history",
                        conversationId: entry.id,
                        timestamp: entry.created_at || null,
                        deferScroll: true
                    });
                    renderMessage("ai", entry.answer || "", false, {
                        speakable: false,
                        source: "history",
                        conversationId: entry.id,
                        feedback: entry.feedback || "",
                        timestamp: entry.created_at || null,
                        deferScroll: true
                    });
                }

                if (index < chronological.length) {
                    window.requestAnimationFrame(drawNextChunk);
                    return;
                }
                scrollToBottom();
            };

            window.requestAnimationFrame(drawNextChunk);
        } catch (_error) {
            renderMessage("ai", "Unable to load conversation memory right now.", false, { speakable: false, source: "error" });
        }
    }

    async function renderTimeline() {
        const timelineNode = ensureTimelineEl();
        if (!timelineNode) {
            return;
        }
        try {
            const response = await fetch("/api/jarvis/history/timeline?days=60");
            const payload = await response.json().catch(function () { return {}; });
            const timeline = Array.isArray(payload.timeline) ? payload.timeline : [];

            if (timeline.length === 0) {
                timelineNode.innerHTML = '<div class="jarvis-timeline-item"><strong>No timeline data</strong>Start a conversation to build your timeline.</div>';
                return;
            }

            timelineNode.innerHTML = timeline.slice(0, 8).map(function (item) {
                return '<div class="jarvis-timeline-item"><strong>' + (item.day || "Unknown day") + '</strong>' +
                    'Conversations: ' + Number(item.conversation_count || 0) + '</div>';
            }).join("");
        } catch (_error) {
            timelineNode.innerHTML = '<div class="jarvis-timeline-item"><strong>Timeline unavailable</strong>Please try again.</div>';
        }
    }

    async function clearMemory() {
        const response = await fetch("/api/jarvis/history", { method: "DELETE" });
        if (!response.ok) {
            throw new Error("Unable to clear memory.");
        }
        return response.json().catch(function () { return {}; });
    }

    async function deleteConversation(conversationId) {
        const response = await fetch("/api/jarvis/history/" + encodeURIComponent(String(conversationId)), {
            method: "DELETE"
        });
        if (!response.ok) {
            throw new Error("Unable to delete conversation.");
        }
    }

    async function exportChat() {
        const response = await fetch("/api/jarvis/history/export");
        if (!response.ok) {
            throw new Error("Unable to export chat.");
        }
        return response.json();
    }

    async function generateFinancialReportSummary() {
        const response = await fetch("/api/jarvis/report/summary");
        if (!response.ok) {
            throw new Error("Unable to generate financial report.");
        }
        return response.json();
    }

    async function emailFinancialReport() {
        const response = await fetch("/api/jarvis/report/email", {
            method: "POST"
        });
        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok) {
            throw new Error(payload.error || "Unable to email report right now.");
        }
        return payload;
    }

    async function sendMessage(message, userBubble) {
        const loadingBubble = renderMessage("ai", "", true);
        setBusy(true);

        try {
            const response = await fetch("/api/jarvis/chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({ message: message })
            });

            const payload = await response.json().catch(function () {
                return {};
            });

            loadingBubble.remove();

            if (!response.ok) {
                const errorText = payload.error || "Jarvis is unavailable right now. Please try again.";
                renderMessage("ai", errorText, false, { speakable: false, source: "error" });
                return;
            }

            const reply = payload.reply || "I could not generate a response just now.";
            const historyId = payload.history_id;
            if (historyId && userBubble) {
                userBubble.dataset.conversationId = String(historyId);
            }
            renderMessage("ai", reply, false, { speakable: true, source: "assistant", conversationId: historyId, animate: true });
        } catch (_error) {
            loadingBubble.remove();
            renderMessage("ai", "Network issue while contacting Jarvis. Please check your connection and try again.", false, { speakable: false, source: "error" });
        } finally {
            setBusy(false);
            input.focus();
        }
    }

    async function loadCoachBriefingIfPending() {
        try {
            const stateResponse = await fetch("/api/jarvis/coach/state");
            if (!stateResponse.ok) {
                return;
            }
            const statePayload = await stateResponse.json().catch(function () { return {}; });
            if (!statePayload.coach_pending) {
                return;
            }

            const briefingResponse = await fetch("/api/jarvis/coach/briefing", {
                method: "POST"
            });
            if (!briefingResponse.ok) {
                return;
            }
            const briefingPayload = await briefingResponse.json().catch(function () { return {}; });
            const coachMarkdown = briefingPayload.message_markdown || "";
            if (coachMarkdown.trim()) {
                renderMessage("ai", coachMarkdown, false, {
                    speakable: false,
                    source: "coach",
                    animate: true
                });
            }
        } catch (_error) {
            return;
        }
    }

    function submitOutgoingMessage(rawMessage) {
        if (isWaiting) {
            return;
        }

        const message = (rawMessage || "").trim();
        if (!message) {
            return;
        }

        const userBubble = renderMessage("user", message, false);
        input.value = "";
        sendMessage(message, userBubble);
    }

    renderHistory("").then(function () {
        loadCoachBriefingIfPending();
    });

    autoResizeInput();

    fab.addEventListener("click", togglePanel);

    if (closeBtn) {
        closeBtn.addEventListener("click", closePanel);
    }

    if (popupManager) {
        if (searchToggle && searchCard) {
            popupManager.registerPopup(searchToggle, searchCard, {
                onOpen: function () {
                    if (searchInput) {
                        searchInput.focus();
                    }
                },
                onClose: function () {
                    if (searchInput) {
                        searchInput.value = "";
                    }
                },
            });
        }

        if (toolsToggle && toolsMenu) {
            popupManager.registerPopup(toolsToggle, toolsMenu, {
                onOpen: function () {
                    refreshThreads().catch(function () { return; });
                },
            });
        }
    }

    if (suggestionsToggle) {
        suggestionsToggle.addEventListener("click", function () {
            toggleSuggestions();
        });
    }

    if (clearBtn) {
        clearBtn.addEventListener("click", function () {
            closeToolsMenu();
            renderWelcomeScreen();
            document.dispatchEvent(new CustomEvent("jarvis:cleared"));
            input.focus();
        });
    }

    if (clearMemoryBtn) {
        clearMemoryBtn.addEventListener("click", async function () {
            closeToolsMenu();
            try {
                await clearMemory();
                renderWelcomeScreen();
                document.dispatchEvent(new CustomEvent("jarvis:cleared"));
                if (timelineEl && !timelineEl.hidden) {
                    renderTimeline();
                }
            } catch (_error) {
                renderMessage("ai", "Unable to clear memory right now.", false, { speakable: false, source: "error" });
            }
        });
    }

    if (searchBtn) {
        searchBtn.addEventListener("click", function () {
            if (!searchInput) {
                return;
            }
            const query = searchInput.value.trim();
            renderHistory(query);
            closeSearchCard();
        });
    }

    if (searchInput) {
        searchInput.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                renderHistory(searchInput.value);
                closeSearchCard();
            }
            if (event.key === "Escape" && !panel.hidden) {
                event.preventDefault();
                closeSearchCard();
            }
        });
    }

    if (timelineBtn) {
        timelineBtn.addEventListener("click", function () {
            const timelineNode = ensureTimelineEl();
            if (!timelineNode) {
                return;
            }
            timelineNode.hidden = !timelineNode.hidden;
            timelineBtn.setAttribute("aria-expanded", timelineNode.hidden ? "false" : "true");
            if (!timelineNode.hidden) {
                renderTimeline();
            }
            closeToolsMenu();
            closeSearchCard();
        });
    }

    if (exportBtn) {
        exportBtn.addEventListener("click", async function () {
            closeToolsMenu();
            try {
                const payload = await exportChat();
                const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
                const url = URL.createObjectURL(blob);
                const link = document.createElement("a");
                link.href = url;
                link.download = "jarvis_chat_export.json";
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            } catch (_error) {
                renderMessage("ai", "Unable to export chat right now.", false, { speakable: false, source: "error" });
            }
        });
    }

    if (generateReportBtn) {
        generateReportBtn.addEventListener("click", async function () {
            closeToolsMenu();
            if (isWaiting) {
                return;
            }

            const loadingBubble = renderMessage("ai", "", true);
            setBusy(true);
            try {
                const payload = await generateFinancialReportSummary();
                loadingBubble.remove();
                renderMessage("ai", payload.markdown || "Financial report generated.", false, {
                    speakable: false,
                    source: "report",
                    animate: true
                });
            } catch (_error) {
                loadingBubble.remove();
                renderMessage("ai", "Unable to generate report right now. Please try again.", false, {
                    speakable: false,
                    source: "error"
                });
            } finally {
                setBusy(false);
                input.focus();
            }
        });
    }

    if (downloadReportBtn) {
        downloadReportBtn.addEventListener("click", function () {
            closeToolsMenu();
            window.open("/api/jarvis/report/pdf", "_blank", "noopener,noreferrer");
        });
    }

    if (emailReportBtn) {
        emailReportBtn.addEventListener("click", async function () {
            closeToolsMenu();
            if (isWaiting) {
                return;
            }

            setBusy(true);
            try {
                const payload = await emailFinancialReport();
                renderMessage("ai", payload.message || "Report emailed successfully.", false, {
                    speakable: false,
                    source: "report",
                    animate: true
                });
            } catch (error) {
                renderMessage("ai", error && error.message ? error.message : "Unable to email report right now.", false, {
                    speakable: false,
                    source: "error"
                });
            } finally {
                setBusy(false);
                input.focus();
            }
        });
    }

    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            if (!isWaiting) {
                form.requestSubmit();
            }
        }
    });

    input.addEventListener("input", autoResizeInput);

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        submitOutgoingMessage(input.value);
        autoResizeInput();
    });

    messagesEl.addEventListener("click", function (event) {
        const button = event.target.closest("button");
        if (!button || !messagesEl.contains(button)) {
            return;
        }

        const bubble = button.closest(".jarvis-message");
        const conversationId = bubble ? (bubble.dataset.conversationId || "") : "";

        if (button.classList.contains("jarvis-delete-btn")) {
            if (!conversationId) {
                return;
            }
            deleteConversation(conversationId).then(function () {
                if (bubble && bubble.parentNode) {
                    bubble.remove();
                }
                renderMessage("ai", "Conversation deleted.", false, { speakable: false, source: "system" });
            }).catch(function () {
                renderMessage("ai", "Unable to delete conversation right now.", false, { speakable: false, source: "error" });
            });
            return;
        }

        if (button.classList.contains("jarvis-copy-btn")) {
            const rawText = bubble ? (bubble.dataset.rawText || "") : "";
            if (!rawText) {
                return;
            }
            navigator.clipboard.writeText(rawText).then(function () {
                button.classList.add("is-active");
                setTimeout(function () {
                    button.classList.remove("is-active");
                }, 650);
            }).catch(function () {
                renderMessage("ai", "Unable to copy response.", false, { speakable: false, source: "error" });
            });
            return;
        }

        if (button.classList.contains("jarvis-regenerate-btn")) {
            const prompt = getConversationUserPrompt(conversationId, bubble);
            if (!prompt.trim()) {
                renderMessage("ai", "Unable to find the original prompt to regenerate.", false, { speakable: false, source: "error" });
                return;
            }
            submitOutgoingMessage(prompt);
            return;
        }

        if (button.classList.contains("jarvis-like-btn") || button.classList.contains("jarvis-dislike-btn")) {
            if (!bubble || !conversationId) {
                return;
            }
            const intended = button.classList.contains("jarvis-like-btn") ? "like" : "dislike";
            const current = bubble.dataset.feedback || "";
            const next = current === intended ? "" : intended;
            setConversationFeedback(conversationId, next).then(function () {
                setFeedbackVisualState(bubble, next);
            }).catch(function () {
                renderMessage("ai", "Unable to store feedback right now.", false, { speakable: false, source: "error" });
            });
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.altKey && event.key.toLowerCase() === "j") {
            event.preventDefault();
            togglePanel();
        }
        if (event.key === "Escape" && !panel.hidden) {
            closePanel();
        }
    });

    window.jarvisChat = {
        renderSystemMessage: function (text) {
            renderMessage("ai", text, false, { speakable: false, source: "system" });
        },
        attachDeleteAction: function (bubble) {
            if (!bubble || !bubble.classList || !bubble.classList.contains("ai")) {
                return;
            }
            ensureUtilityActions(bubble);
        }
    };
})();
