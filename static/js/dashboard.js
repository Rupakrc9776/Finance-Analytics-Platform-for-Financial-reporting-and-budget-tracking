document.addEventListener("DOMContentLoaded", () => {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const root = document.documentElement;
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

    const resolveTheme = () => {
        const stored = localStorage.getItem("sfi-theme-mode");
        const current = root.getAttribute("data-theme-resolved");
        if (stored === "light" || stored === "dark") {
            root.setAttribute("data-theme-resolved", stored);
            return stored;
        }
        if (current === "light" || current === "dark") {
            return current;
        }
        root.setAttribute("data-theme-resolved", "light");
        return "light";
    };

    const applyThemeIconState = (theme) => {
        const themeToggle = document.getElementById("themeToggle");
        if (!themeToggle) {
            return;
        }
        themeToggle.setAttribute("data-mode", theme);
        themeToggle.setAttribute("aria-pressed", String(theme === "dark"));
        themeToggle.setAttribute("title", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
    };

    const syncThemePreference = async (theme) => {
        if (!csrfToken) {
            return;
        }

        try {
            const payload = new URLSearchParams();
            payload.set("csrf_token", csrfToken);
            payload.set("theme_mode", theme);

            await fetch("/theme-mode", {
                method: "POST",
                headers: {
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: payload.toString(),
                credentials: "same-origin",
            });
        } catch (_) {
            // Non-blocking: local theme still applies if network save fails.
        }
    };

    const toRgba = (color, alpha, fallback = "transparent") => {
        if (!color) {
            return fallback;
        }

        const value = color.trim();
        if (value.startsWith("#")) {
            let hex = value.slice(1);
            if (hex.length === 3) {
                hex = hex.split("").map((part) => part + part).join("");
            }
            if (hex.length !== 6) {
                return fallback;
            }

            const parsed = Number.parseInt(hex, 16);
            if (!Number.isFinite(parsed)) {
                return fallback;
            }

            const red = (parsed >> 16) & 255;
            const green = (parsed >> 8) & 255;
            const blue = parsed & 255;
            return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
        }

        const rgb = value.match(/^rgba?\(([^)]+)\)$/i);
        if (!rgb) {
            return fallback;
        }

        const channels = rgb[1].split(",").slice(0, 3).map((part) => part.trim());
        if (channels.length !== 3) {
            return fallback;
        }

        return `rgba(${channels.join(", ")}, ${alpha})`;
    };

    const getThemeChartPalette = () => {
        const computed = getComputedStyle(root);
        const getToken = (token, fallback) => computed.getPropertyValue(token).trim() || fallback;
        const primary = getToken("--primary", "");
        const secondary = getToken("--secondary", primary);
        const success = getToken("--success", primary);
        const warning = getToken("--warning", primary);
        const danger = getToken("--danger", primary);
        const textLight = getToken("--text-light", getToken("--theme-muted", ""));

        return {
            series1: getToken("--theme-chart-series-1", primary),
            series2: getToken("--theme-chart-series-2", success),
            series3: getToken("--theme-chart-series-3", warning),
            series4: getToken("--theme-chart-series-4", danger),
            series5: getToken("--theme-chart-series-5", secondary),
            series6: getToken("--theme-chart-series-6", secondary),
            series7: getToken("--theme-chart-series-7", success),
            series8: getToken("--theme-chart-series-8", warning),
            accentStrong: getToken("--theme-accent-strong", primary),
            text: getToken("--theme-chart-text", textLight),
            grid: getToken("--theme-chart-grid", getToken("--theme-border", "transparent")),
        };
    };

    const themedChartBindings = [];

    const applyThemedChartBinding = (binding) => {
        if (!binding || !binding.chart || typeof binding.applyTheme !== "function") {
            return;
        }

        try {
            binding.applyTheme(binding.chart, getThemeChartPalette(), toRgba);
            binding.chart.update("none");
        } catch (_) {
            // Ignore stale or destroyed chart references.
        }
    };

    const registerThemeAwareChart = (chart, applyTheme) => {
        if (!chart || typeof applyTheme !== "function") {
            return;
        }

        const binding = { chart, applyTheme };
        themedChartBindings.push(binding);
        applyThemedChartBinding(binding);
    };

    window.getThemeChartPalette = getThemeChartPalette;
    window.themeColorToRgba = toRgba;
    window.registerThemeAwareChart = registerThemeAwareChart;

    const applyChartTheme = () => {
        if (typeof Chart === "undefined") {
            return;
        }

        const computed = getComputedStyle(root);
        const textColor = computed.getPropertyValue("--theme-chart-text").trim() || computed.getPropertyValue("--theme-muted").trim();
        const borderColor = computed.getPropertyValue("--theme-chart-grid").trim() || computed.getPropertyValue("--theme-border").trim();
        const tooltipBg = computed.getPropertyValue("--theme-tooltip-bg").trim() || computed.getPropertyValue("--theme-surface").trim();
        const tooltipBody = computed.getPropertyValue("--theme-tooltip-body").trim() || computed.getPropertyValue("--theme-text").trim();

        Chart.defaults.color = textColor;
        Chart.defaults.borderColor = borderColor;
        Chart.defaults.plugins.tooltip.backgroundColor = tooltipBg;
        Chart.defaults.plugins.tooltip.bodyColor = tooltipBody;

        Object.values(Chart.instances || {}).forEach((chart) => chart.update("none"));
    };

    const setTheme = (theme, persistRemote = false) => {
        const mode = theme === "dark" ? "dark" : "light";
        root.setAttribute("data-theme-resolved", mode);
        localStorage.setItem("sfi-theme-mode", mode);
        applyThemeIconState(mode);
        applyChartTheme();
        document.dispatchEvent(new CustomEvent("sfi:theme-change", { detail: { mode } }));

        if (persistRemote) {
            syncThemePreference(mode);
        }
    };

    const initialTheme = resolveTheme();
    applyThemeIconState(initialTheme);

    const showToast = (message, variant = "info") => {
        if (!message) {
            return;
        }

        let container = document.getElementById("toastContainer");
        if (!container) {
            container = document.createElement("div");
            container.id = "toastContainer";
            container.className = "toast-container";
            document.body.appendChild(container);
        }

        const toast = document.createElement("div");
        toast.className = `toast-message ${variant}`;
        toast.innerHTML = `<span>${message}</span><button type="button" aria-label="Dismiss toast">×</button>`;
        container.appendChild(toast);

        const dismiss = () => {
            toast.classList.add("hide");
            setTimeout(() => toast.remove(), 180);
        };

        const button = toast.querySelector("button");
        if (button) {
            button.addEventListener("click", dismiss);
        }

        setTimeout(dismiss, 4200);
    };

    document.querySelectorAll(".flash-message").forEach((node) => {
        const text = node.textContent?.trim();
        let variant = "info";

        if (node.classList.contains("success")) {
            variant = "success";
        } else if (node.classList.contains("danger")) {
            variant = "danger";
        } else if (node.classList.contains("warning")) {
            variant = "warning";
        }

        showToast(text, variant);
        node.remove();
    });

    if (typeof Chart !== "undefined") {
        const valueFormatter = new Intl.NumberFormat("en-IN", {
            minimumFractionDigits: 0,
            maximumFractionDigits: 2,
        });

        const toNumber = (input) => {
            const value = Number(input);
            return Number.isFinite(value) ? value : 0;
        };

        const getDatasetTotal = (chart, datasetIndex) => {
            const points = chart?.data?.datasets?.[datasetIndex]?.data;

            if (!Array.isArray(points)) {
                return 0;
            }

            return points.reduce((sum, item) => sum + Math.abs(toNumber(item)), 0);
        };

        const formatCompactValue = (value) => valueFormatter.format(toNumber(value));

        Chart.defaults.responsive = true;
        Chart.defaults.maintainAspectRatio = false;
        Chart.defaults.font.family = "Poppins, system-ui, sans-serif";
        const computed = getComputedStyle(root);
        Chart.defaults.color = computed.getPropertyValue("--theme-chart-text").trim() || computed.getPropertyValue("--theme-muted").trim();
        Chart.defaults.layout = { padding: { top: 6, right: 8, bottom: 4, left: 8 } };
        Chart.defaults.plugins.legend.position = "bottom";
        Chart.defaults.plugins.legend.align = "start";
        Chart.defaults.plugins.legend.labels.usePointStyle = true;
        Chart.defaults.plugins.legend.labels.pointStyle = "circle";
        Chart.defaults.plugins.legend.labels.boxWidth = 10;
        Chart.defaults.plugins.legend.labels.boxHeight = 10;
        Chart.defaults.plugins.legend.labels.padding = 10;
        Chart.defaults.plugins.legend.labels.font = { size: 11, weight: "600" };
        Chart.defaults.plugins.tooltip.backgroundColor = computed.getPropertyValue("--theme-tooltip-bg").trim() || computed.getPropertyValue("--theme-surface").trim();
        Chart.defaults.plugins.tooltip.titleColor = computed.getPropertyValue("--theme-text").trim();
        Chart.defaults.plugins.tooltip.bodyColor = computed.getPropertyValue("--theme-tooltip-body").trim() || computed.getPropertyValue("--theme-text").trim();
        Chart.defaults.plugins.tooltip.displayColors = true;
        Chart.defaults.plugins.tooltip.padding = 10;
        Chart.defaults.plugins.tooltip.cornerRadius = 8;
        Chart.defaults.plugins.tooltip.titleFont = { size: 11, weight: "700" };
        Chart.defaults.plugins.tooltip.bodyFont = { size: 11, weight: "500" };
        Chart.defaults.plugins.tooltip.callbacks = {
            label(context) {
                const value = toNumber(context.raw ?? context.parsed?.y ?? context.parsed ?? 0);
                const total = getDatasetTotal(context.chart, context.datasetIndex);
                const percentage = total > 0 ? ((Math.abs(value) / total) * 100) : 0;
                const datasetLabel = context.dataset.label || context.label || "Value";
                const formatted = formatCompactValue(value);

                if (total > 0) {
                    return `${datasetLabel}: ${formatted} (${percentage.toFixed(1)}%)`;
                }

                return `${datasetLabel}: ${formatted}`;
            }
        };
        Chart.defaults.borderColor = computed.getPropertyValue("--theme-chart-grid").trim() || computed.getPropertyValue("--theme-border").trim();
        Chart.defaults.animation.duration = reduceMotion ? 0 : 560;
        Chart.defaults.animation.easing = "easeOutCubic";
    }

    const animatedCards = document.querySelectorAll(".card, .form-card, .table-card, .chart-card, .info-card, .glass-card");

    animatedCards.forEach((card, index) => {
        if (reduceMotion) {
            card.style.opacity = "1";
            card.style.transform = "none";
            return;
        }

        card.style.opacity = "0";
        card.style.transform = "translateY(10px)";

        setTimeout(() => {
            card.style.transition = "opacity .28s ease, transform .28s ease";
            card.style.opacity = "1";
            card.style.transform = "translateY(0)";
        }, index * 40);
    });

    const menuBtn = document.getElementById("menuBtn");
    const sidebar = document.querySelector(".sidebar");
    const mainContent = document.querySelector(".main-content");

    if (menuBtn && sidebar && mainContent) {
        menuBtn.addEventListener("click", () => {
            sidebar.classList.toggle("active");
            mainContent.classList.toggle("full");
        });
    }

    const currentPath = window.location.pathname;
    const navLinks = document.querySelectorAll(".sidebar-nav a");

    navLinks.forEach((link) => {
        const href = link.getAttribute("href");
        if (href === currentPath) {
            link.classList.add("active");
        }
    });

    const notificationToggle = document.getElementById("notificationToggle");
    const notificationDropdown = document.getElementById("notificationDropdown");

    if (notificationToggle && notificationDropdown) {
        notificationToggle.addEventListener("click", (event) => {
            event.stopPropagation();
            notificationDropdown.classList.toggle("open");
        });

        document.addEventListener("click", (event) => {
            if (!notificationDropdown.contains(event.target) && !notificationToggle.contains(event.target)) {
                notificationDropdown.classList.remove("open");
            }
        });
    }

    const profileToggle = document.getElementById("profileToggle");
    const profileDropdown = document.getElementById("profileDropdown");
    const themeToggle = document.getElementById("themeToggle");

    if (profileToggle && profileDropdown) {
        profileToggle.addEventListener("click", (event) => {
            event.stopPropagation();
            profileDropdown.classList.toggle("open");
            profileToggle.classList.toggle("active");
        });

        document.addEventListener("click", (event) => {
            if (!profileDropdown.contains(event.target) && !profileToggle.contains(event.target)) {
                profileDropdown.classList.remove("open");
                profileToggle.classList.remove("active");
            }
        });
    }

    if (themeToggle) {
        themeToggle.addEventListener("click", () => {
            const current = root.getAttribute("data-theme-resolved") === "dark" ? "dark" : "light";
            const next = current === "dark" ? "light" : "dark";
            setTheme(next, true);
        });
    }

    const sortableList = document.getElementById("dashboardOrderList");
    const cardOrderInput = document.getElementById("cardOrderInput");
    const prefForm = document.getElementById("dashboardPrefForm");

    if (sortableList) {
        let draggedElement = null;

        sortableList.querySelectorAll("li").forEach((item) => {
            item.addEventListener("dragstart", () => {
                draggedElement = item;
                item.classList.add("dragging");
            });

            item.addEventListener("dragend", () => {
                item.classList.remove("dragging");
                draggedElement = null;
            });

            item.addEventListener("dragover", (event) => {
                event.preventDefault();
                if (!draggedElement || draggedElement === item) {
                    return;
                }

                const rect = item.getBoundingClientRect();
                const shouldInsertBefore = event.clientY < rect.top + rect.height / 2;
                if (shouldInsertBefore) {
                    sortableList.insertBefore(draggedElement, item);
                } else {
                    sortableList.insertBefore(draggedElement, item.nextSibling);
                }
            });
        });
    }

    if (prefForm && cardOrderInput && sortableList) {
        prefForm.addEventListener("submit", () => {
            const orderedKeys = Array.from(sortableList.querySelectorAll("li"))
                .map((item) => item.dataset.cardKey)
                .filter(Boolean);
            cardOrderInput.value = orderedKeys.join(",");
        });
    }

    document.querySelectorAll(".chart-card, .table-card, .form-card").forEach((section) => {
        section.classList.add("section-ready");
    });

    document.querySelectorAll("[data-width]").forEach((el) => {
        const value = Number.parseFloat(el.getAttribute("data-width") || "0");
        const clamped = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
        el.style.width = `${clamped}%`;
    });

    document.querySelectorAll(".gauge-ring[data-score]").forEach((ring) => {
        const value = Number.parseFloat(ring.getAttribute("data-score") || "0");
        const clamped = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
        ring.style.setProperty("--score", `${clamped}`);
    });

    applyChartTheme();

    if (themedChartBindings.length > 0) {
        themedChartBindings.forEach((binding) => applyThemedChartBinding(binding));
    }
});