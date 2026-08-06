(function () {
    const registrations = [];
    let bootstrapped = false;

    function getPopupElement(elementOrSelector) {
        if (!elementOrSelector) {
            return null;
        }
        if (typeof elementOrSelector === "string") {
            return document.querySelector(elementOrSelector);
        }
        return elementOrSelector;
    }

    function setOpen(popup, open) {
        if (!popup) {
            return;
        }

        if (open) {
            popup.hidden = false;
            window.requestAnimationFrame(function () {
                popup.classList.add("is-open");
            });
            return;
        }

        popup.classList.remove("is-open");
        window.setTimeout(function () {
            if (!popup.classList.contains("is-open")) {
                popup.hidden = true;
            }
        }, 190);
    }

    function isOpen(entry) {
        return entry && entry.popup && !entry.popup.hidden;
    }

    function closeEntry(entry) {
        if (!entry || !entry.popup) {
            return;
        }
        setOpen(entry.popup, false);
        if (entry.trigger) {
            entry.trigger.setAttribute("aria-expanded", "false");
            entry.trigger.classList.remove("is-active");
        }
        if (typeof entry.onClose === "function") {
            entry.onClose();
        }
    }

    function openEntry(entry) {
        if (!entry || !entry.popup) {
            return;
        }
        registrations.forEach(function (other) {
            if (other !== entry) {
                closeEntry(other);
            }
        });
        setOpen(entry.popup, true);
        if (entry.trigger) {
            entry.trigger.setAttribute("aria-expanded", "true");
            entry.trigger.classList.add("is-active");
        }
        if (typeof entry.onOpen === "function") {
            entry.onOpen();
        }
    }

    function closeAllPopups() {
        registrations.forEach(function (entry) {
            closeEntry(entry);
        });
    }

    function registerPopup(trigger, popup, options) {
        const triggerEl = getPopupElement(trigger);
        const popupEl = getPopupElement(popup);
        if (!triggerEl || !popupEl) {
            return null;
        }

        const entry = {
            trigger: triggerEl,
            popup: popupEl,
            onOpen: options && typeof options.onOpen === "function" ? options.onOpen : null,
            onClose: options && typeof options.onClose === "function" ? options.onClose : null,
        };
        registrations.push(entry);

        triggerEl.addEventListener("click", function (event) {
            event.preventDefault();
            event.stopPropagation();
            if (isOpen(entry)) {
                closeEntry(entry);
                return;
            }
            openEntry(entry);
        });

        popupEl.addEventListener("click", function (event) {
            event.stopPropagation();
        });

        return {
            open: function () {
                openEntry(entry);
            },
            close: function () {
                closeEntry(entry);
            },
            toggle: function () {
                if (isOpen(entry)) {
                    closeEntry(entry);
                    return;
                }
                openEntry(entry);
            },
            element: popupEl,
            trigger: triggerEl,
        };
    }

    function bootstrap() {
        if (bootstrapped) {
            return;
        }
        bootstrapped = true;

        document.addEventListener("click", function (event) {
            const target = event.target;
            const insidePopup = registrations.some(function (entry) {
                return entry.popup.contains(target) || entry.trigger.contains(target);
            });
            if (!insidePopup) {
                closeAllPopups();
            }
        });

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape") {
                closeAllPopups();
            }
        });
    }

    window.jarvisPopupManager = {
        registerPopup: registerPopup,
        closeAllPopups: closeAllPopups,
        bootstrap: bootstrap,
    };

    bootstrap();
})();
