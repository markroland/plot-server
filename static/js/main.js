
(() => {
    const storedTheme = localStorage.getItem('plot-server-theme');
    const preferredTheme = storedTheme || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = preferredTheme;
})();


// Polling interval ID
let busyPollingInterval = null;
let plotRequestInFlight = false;
let servoRequestInFlight = false;
let stopRequestInFlight = false;

// Global variables to store plotter and SVG data
let currentPlotterData = null;
let currentSvgDimensions = null;
let currentPreviewEstimate = null;
let currentAnimator = null;
let currentPreviewObjectUrl = null;
let isPlaybackActive = false;
let plotCountdownInterval = null;
let plotCountdownEndTimeMs = null;
let preserveCountdownAfterStop = false;
let previewLoadRequestId = 0;
let plotLogEntries = [];
let activePlotLogEntryId = null;
const INKSCAPE_NAMESPACE = 'http://www.inkscape.org/namespaces/inkscape';

function setText(selector, value) {
    const element = document.querySelector(selector);
    if (element) {
        element.textContent = value;
    }
}

function hasSelectedFilename() {
    const filenameInput = document.querySelector('form[name=plot] input[name=filename]');
    return Boolean(filenameInput && filenameInput.value);
}

function doesCurrentSvgFitPlotter() {
    if (!currentSvgDimensions || !currentPlotterData?.config?.x_travel || !currentPlotterData?.config?.y_travel) {
        return null;
    }

    const svgWidth = currentSvgDimensions.width;
    const svgHeight = currentSvgDimensions.height;
    const plotterWidth = currentPlotterData.config.x_travel * 25.4;
    const plotterHeight = currentPlotterData.config.y_travel * 25.4;

    return svgWidth <= plotterWidth && svgHeight <= plotterHeight;
}

function syncControlButtons() {
    const plotButton = document.querySelector('#submit_plot');
    const toggleButton = document.querySelector('#toggle-servo-button');
    const stopButton = document.querySelector('#stop-plot-button');

    if (!plotButton || !toggleButton || !stopButton) {
        return;
    }

    const status = currentPlotterData?.status;
    const requestInFlight = plotRequestInFlight || servoRequestInFlight || stopRequestInFlight;
    const hasFile = hasSelectedFilename();
    const fitsPlotter = doesCurrentSvgFitPlotter();
    const isPlotRunning = currentPlotterData?.plot_state === 'plotting' || plotRequestInFlight;
    const canPlot = status === 'on' && hasFile && fitsPlotter !== false && !requestInFlight;
    const canToggleServo = status === 'on' && !requestInFlight;
    const canStopPlot = isPlotRunning && !stopRequestInFlight;

    plotButton.disabled = !canPlot;
    toggleButton.disabled = !canToggleServo;
    stopButton.disabled = !canStopPlot;
}

function setBusyStatusLocally() {
    const fallbackData = {
        status: 'busy',
        plot_state: 'plotting',
        machine: currentPlotterData?.machine || 'Unknown',
        config: currentPlotterData?.config || {},
    };

    updatePlotterStatus({ ...currentPlotterData, ...fallbackData });
}

async function refreshPlotterStatus() {
    try {
        const statusResponse = await fetch('/status.json', { cache: 'no-store' });
        if (statusResponse.ok) {
            updatePlotterStatus(await statusResponse.json());
            return;
        }
    } catch (error) {
        console.error('Failed to refresh plotter status:', error);
    }

    syncControlButtons();
}

function setSvgSourceText(svgMarkup) {
    const sourceElement = document.querySelector('#svg-source-text');
    if (!sourceElement) {
        return;
    }

    sourceElement.textContent = svgMarkup || 'No SVG loaded.';
}

function setInfoModalOpen(isOpen) {
    const modalElement = document.querySelector('#info-modal');
    if (!modalElement) {
        return;
    }

    modalElement.hidden = !isOpen;
    document.body.classList.toggle('modal-open', isOpen);

    if (isOpen) {
        document.querySelector('#info-modal-close')?.focus();
    } else {
        document.querySelector('#info-link')?.focus();
    }
}

function initializeInfoModal() {
    const modalElement = document.querySelector('#info-modal');
    const openLink = document.querySelector('#info-link');
    const closeButton = document.querySelector('#info-modal-close');

    if (!modalElement || !openLink || !closeButton) {
        return;
    }

    openLink.addEventListener('click', function(event) {
        event.preventDefault();
        setInfoModalOpen(true);
    });

    closeButton.addEventListener('click', function() {
        setInfoModalOpen(false);
    });

    modalElement.addEventListener('click', function(event) {
        if (event.target instanceof HTMLElement && event.target.dataset.closeModal === 'true') {
            setInfoModalOpen(false);
        }
    });

    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape' && !modalElement.hidden) {
            setInfoModalOpen(false);
        }
    });
}

function getInkscapeLabel(element) {
    return (
        element.getAttribute('inkscape:label')
        || element.getAttributeNS(INKSCAPE_NAMESPACE, 'label')
        || ''
    ).trim();
}

function extractPlottableLayers(svgElement) {
    const seenLabels = new Set();
    const layers = [];

    svgElement.querySelectorAll('g').forEach((groupElement) => {
        const label = getInkscapeLabel(groupElement);
        const match = label.match(/^([1-9]\d*)(?:\b|\D)/);

        if (!match || seenLabels.has(label)) {
            return;
        }

        seenLabels.add(label);
        layers.push({
            label,
            number: Number(match[1]),
        });
    });

    layers.sort((left, right) => left.number - right.number || left.label.localeCompare(right.label));
    return layers;
}

function buildPreviewRequestQuery(selectedLayerValue) {
    const queryParams = { preview: 'true' };
    if (selectedLayerValue) {
        queryParams.layer = selectedLayerValue;
    }
    return queryParams;
}

function buildPreviewSvgPayload(svgMarkup, selectedLayerValue) {
    const parser = new DOMParser();
    const svgDocument = parser.parseFromString(svgMarkup, 'image/svg+xml');
    const parserError = svgDocument.querySelector('parsererror');
    if (parserError) {
        throw new Error('Failed to parse SVG preview markup');
    }

    const sourceSvgElement = svgDocument.documentElement;
    const availableLayers = extractPlottableLayers(sourceSvgElement);

    if (!selectedLayerValue) {
        return {
            availableLayers,
            previewSvgMarkup: svgMarkup,
        };
    }

    const selectedLayerNumber = Number(selectedLayerValue);
    const filteredSvgElement = sourceSvgElement.cloneNode(true);
    filteredSvgElement.querySelectorAll('g').forEach((groupElement) => {
        const label = getInkscapeLabel(groupElement);
        const match = label.match(/^([1-9]\d*)(?:\b|\D)/);
        if (!match) {
            return;
        }

        if (Number(match[1]) !== selectedLayerNumber) {
            groupElement.remove();
        }
    });

    return {
        availableLayers,
        previewSvgMarkup: new XMLSerializer().serializeToString(filteredSvgElement),
    };
}

function revokeCurrentPreviewObjectUrl() {
    if (!currentPreviewObjectUrl) {
        return;
    }

    URL.revokeObjectURL(currentPreviewObjectUrl);
    currentPreviewObjectUrl = null;
}

function scrollLibraryItemIntoView(listItem) {
    if (!listItem) {
        return;
    }

    listItem.scrollIntoView({ block: 'center', inline: 'nearest' });
}

function setSelectedPlot(filename) {
    setText("#selected-sketch-name", `File: ${filename || "No plot selected"}`);
    updateDownloadLinks(filename);
}

function buildPlotRequestPath(filename, queryParams = {}) {
    const requestPath = `/plot/${filename.split('/').map(encodeURIComponent).join('/')}`;
    const params = new URLSearchParams(queryParams);
    const queryString = params.toString();
    return queryString ? `${requestPath}?${queryString}` : requestPath;
}

function buildDownloadRequestPath(filename) {
    return `/download/${filename.split('/').map(encodeURIComponent).join('/')}`;
}

function buildDeleteRequestPath(filename) {
    return `/files/${filename.split('/').map(encodeURIComponent).join('/')}`;
}

function getRequestedLayerValue() {
    return new URLSearchParams(window.location.search).get('layer') || '';
}

function updateLayerQueryString(layerValue) {
    const url = new URL(window.location);
    if (layerValue) {
        url.searchParams.set('layer', layerValue);
    } else {
        url.searchParams.delete('layer');
    }
    window.history.replaceState({}, '', url);
}

function updateDownloadLink(linkSelector, filename, hrefBuilder, downloadNameBuilder) {
    const downloadLink = document.querySelector(linkSelector);
    if (!downloadLink) {
        return;
    }

    if (!filename) {
        downloadLink.setAttribute('href', '#');
        downloadLink.setAttribute('aria-disabled', 'true');
        downloadLink.classList.add('is-disabled');
        downloadLink.removeAttribute('download');
        return;
    }

    downloadLink.setAttribute('href', hrefBuilder(filename));
    downloadLink.setAttribute('download', downloadNameBuilder(filename));
    downloadLink.setAttribute('aria-disabled', 'false');
    downloadLink.classList.remove('is-disabled');
}

function updateDeleteButton(filename) {
    const deleteButton = document.querySelector('#delete-file-button');
    if (!deleteButton) {
        return;
    }

    deleteButton.disabled = !filename;
    deleteButton.dataset.filename = filename || '';
}

function buildSvgAssetPath(filename) {
    return `/static/uploads/${filename.split('/').map(encodeURIComponent).join('/')}`;
}

function updateDownloadLinks(filename) {
    updateDownloadLink(
        '#download-svg-link',
        filename,
        buildSvgAssetPath,
        (currentFilename) => currentFilename.split('/').pop()
    );

    updateDownloadLink(
        '#download-pdf-link',
        filename,
        buildDownloadRequestPath,
        (currentFilename) => currentFilename.replace(/\.svg$/i, '.pdf').split('/').pop()
    );

    updateDeleteButton(filename);
}

function clearSelectedPlotState() {
    const previewElement = document.querySelector('#svg-object');
    previewElement.removeAttribute('data-filename');
    previewElement.innerHTML = '';
    currentSvgDimensions = null;
    currentPreviewEstimate = null;
    stopPlotCountdown(true);
    setSvgSourceText('');

    if (currentAnimator) {
        currentAnimator.pause();
    }

    setSelectedPlot('');
    setPlaybackControlsEnabled(false);
    setPlaybackButtonState(false);
    updatePlaybackProgress({ current: 0, total: 0, percentage: 0 });
    document.querySelector('#svg-dimensions').textContent = '-';
    document.querySelector('#plotter-fit').textContent = 'Fit: -';
    document.querySelector('#plotter-fit').className = 'info-tile__meta';
    resetPreviewEstimate('-');
    updatePlotCommand();
    syncControlButtons();
}

async function deleteSelectedFile() {
    const deleteButton = document.querySelector('#delete-file-button');
    const filename = deleteButton?.dataset.filename || '';

    if (!filename) {
        return;
    }

    const confirmed = window.confirm(`Delete ${filename}? This cannot be undone.`);
    if (!confirmed) {
        return;
    }

    deleteButton.disabled = true;

    try {
        const response = await fetch(buildDeleteRequestPath(filename), { method: 'DELETE' });
        if (!response.ok) {
            throw new Error(`Delete request failed with status ${response.status}`);
        }

        const listItem = document.querySelector(`#files li[data-filename="${CSS.escape(filename)}"]`);
        const nextListItem = listItem?.nextElementSibling || listItem?.previousElementSibling || null;
        listItem?.remove();

        const url = new URL(window.location);

        if (nextListItem) {
            const nextFilename = nextListItem.getAttribute('data-filename');
            const nextLink = nextListItem.querySelector('a.file-link');
            document.querySelectorAll('ol#files li.selected').forEach((el) => el.classList.remove('selected'));
            nextListItem.classList.add('selected');
            scrollLibraryItemIntoView(nextListItem);
            url.searchParams.set('plot', nextFilename);
            url.searchParams.delete('layer');
            window.history.replaceState({}, '', url);
            setSelectedPlot(nextFilename);
            await loadAnimatedPreview(nextLink.getAttribute('href'), nextFilename, '');
        } else {
            document.querySelectorAll('ol#files li.selected').forEach((el) => el.classList.remove('selected'));
            url.searchParams.delete('plot');
            url.searchParams.delete('layer');
            window.history.replaceState({}, '', url);
            clearSelectedPlotState();
        }
    } catch (error) {
        console.error('Failed to delete file:', error);
        window.alert('Failed to delete file.');
        deleteButton.disabled = false;
    }
}

function getCurrentSvgElement() {
    return document.querySelector('#svg-object svg');
}

function getSimplePlotAnimatorClass() {
    const animatorModule = window.SimplePlotAnimator;

    if (typeof animatorModule === 'function') {
        return animatorModule;
    }

    if (animatorModule && typeof animatorModule.SimplePlotAnimator === 'function') {
        return animatorModule.SimplePlotAnimator;
    }

    if (animatorModule && typeof animatorModule.default === 'function') {
        return animatorModule.default;
    }

    return null;
}

function waitForBrowserFrame() {
    return new Promise((resolve) => {
        requestAnimationFrame(() => {
            setTimeout(resolve, 0);
        });
    });
}

function setPreviewLoading(isVisible, message = 'Preparing SVG...') {
    const loadingElement = document.querySelector('#preview-loading');
    if (!loadingElement) {
        return;
    }

    document.querySelector('#preview-loading-message').textContent = message;
    loadingElement.hidden = !isVisible;
    loadingElement.classList.toggle('is-visible', isVisible);
    document.querySelector('.artwork-frame').classList.toggle('is-loading', isVisible);
}

function setPlaybackButtonState(isPlaying) {
    isPlaybackActive = isPlaying;
    setText('#play-pause-btn', isPlaying ? 'Pause' : 'Play');
}

function updatePlaybackProgress(progress) {
    const current = Math.min(progress.current, progress.total);
    setText('#progress-text', `${Number(current || 0).toLocaleString()} / ${Number(progress.total || 0).toLocaleString()}`);

    const percentage = progress.total > 0 ? Math.round((current / progress.total) * 100) : 0;
    document.querySelector('#progress-fill').style.width = `${percentage}%`;
}

function setPlaybackControlsEnabled(enabled) {
    document.querySelector('#play-pause-btn').disabled = !enabled;
    document.querySelector('#reset-btn').disabled = !enabled;
    document.querySelector('#speed-slider').disabled = !enabled;
}

function formatDurationFromSeconds(totalSeconds) {
    if (!Number.isFinite(totalSeconds)) {
        return '-';
    }

    if (totalSeconds < 60) {
        return `${Math.round(totalSeconds)} sec`;
    }

    const roundedMinutes = Math.round(totalSeconds / 60);
    const hours = Math.floor(roundedMinutes / 60);
    const minutes = roundedMinutes % 60;

    const parts = [];

    if (hours > 0) {
        parts.push(`${hours} hr`);
    }

    if (minutes > 0 || hours === 0) {
        parts.push(`${minutes} min`);
    }

    return parts.join(' ');
}

function formatMeters(value) {
    if (!Number.isFinite(value)) {
        return '-';
    }

    return `${value.toFixed(2)} m`;
}

function formatCompactTimestamp(unixSeconds = null) {
    const dateValue = Number.isFinite(Number(unixSeconds))
        ? new Date(Number(unixSeconds) * 1000)
        : new Date();

    const year = String(dateValue.getFullYear());
    const month = String(dateValue.getMonth() + 1).padStart(2, '0');
    const day = String(dateValue.getDate()).padStart(2, '0');
    const hours = String(dateValue.getHours()).padStart(2, '0');
    const minutes = String(dateValue.getMinutes()).padStart(2, '0');
    const seconds = String(dateValue.getSeconds()).padStart(2, '0');

    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function formatDurationClock(totalSeconds) {
    if (!Number.isFinite(Number(totalSeconds)) || Number(totalSeconds) < 0) {
        return '-';
    }

    const safeSeconds = Math.floor(Number(totalSeconds));
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const seconds = safeSeconds % 60;

    return [hours, minutes, seconds]
        .map((part) => String(part).padStart(2, '0'))
        .join(':');
}

function getCurrentPlotContext(filename, layer) {
    const titleValue = document.querySelector('input[name=title]')?.value || '';
    const toolValue = document.querySelector('select[name=tool]')?.value || 'None';
    const materialValue = document.querySelector('select[name=material]')?.value || 'None';
    const selectedLayer = (layer ?? document.querySelector('select[name=layer]')?.value ?? '');
    const selectedFilename = filename || document.querySelector('form[name=plot] input[name=filename]')?.value || '';

    const orientation = currentSvgDimensions && currentSvgDimensions.width > currentSvgDimensions.height
        ? 'Landscape'
        : 'Portrait';

    let formatValue = '';
    if (currentSvgDimensions) {
        const widthInches = currentSvgDimensions.width / 25.4;
        const heightInches = currentSvgDimensions.height / 25.4;
        const formatDimension = (value) => {
            const fixed = value.toFixed(1);
            return fixed.endsWith('.0') ? fixed.slice(0, -2) : fixed;
        };
        formatValue = `${formatDimension(widthInches)}x${formatDimension(heightInches)}`;
    }

    return {
        title: titleValue,
        filename: selectedFilename,
        tool: toolValue,
        media: materialValue,
        format: formatValue,
        orientation,
        layer: selectedLayer || 'all',
        edition: 1,
        editions: 1,
        plotter: currentPlotterData?.machine || 'Unknown',
    };
}

function getStatusClass(status) {
    const value = String(status || '').toLowerCase();
    if (value === 'ok') {
        return 'status-ok';
    }
    if (value === 'error' || value === 'stopped') {
        return 'status-error';
    }
    if (value === 'plotting' || value === 'stopping') {
        return 'status-started';
    }
    return '';
}

function formatLogCellValue(value) {
    if (value === null || value === undefined || value === '') {
        return '-';
    }

    return String(value);
}

function updatePlotLogUi() {
    const emptyElement = document.querySelector('#plot-log-empty');
    const tableElement = document.querySelector('#plot-log-table');
    const bodyElement = document.querySelector('#plot-log-body');

    if (!emptyElement || !tableElement || !bodyElement) {
        return;
    }

    if (plotLogEntries.length === 0) {
        emptyElement.hidden = false;
        tableElement.hidden = true;
        bodyElement.innerHTML = '';
        return;
    }

    emptyElement.hidden = true;
    tableElement.hidden = false;

    bodyElement.innerHTML = plotLogEntries.map((entry) => {
        const statusClass = getStatusClass(entry.status);
        return `
            <tr>
                <td>${formatLogCellValue(entry.time)}</td>
                <td class="${statusClass}">${formatLogCellValue(entry.status)}</td>
                <td>${formatLogCellValue(entry.title)}</td>
                <td>${formatLogCellValue(entry.filename)}</td>
                <td>${formatLogCellValue(entry.fileHash)}</td>
                <td>${formatLogCellValue(entry.plotter)}</td>
                <td>${formatLogCellValue(entry.edition)}</td>
                <td>${formatLogCellValue(entry.layer)}</td>
                <td>${formatLogCellValue(entry.tool)}</td>
                <td>${formatLogCellValue(entry.media)}</td>
                <td>${formatLogCellValue(entry.format)}</td>
                <td>${formatLogCellValue(entry.orientation)}</td>
                <td>${formatLogCellValue(entry.duration)}</td>
                <td>${formatLogCellValue(entry.path)}</td>
                <td>${formatLogCellValue(entry.travel)}</td>
                <td>${formatLogCellValue(entry.lifts)}</td>
            </tr>
        `;
    }).join('');
}

function addPlotLogEntry(entry) {
    plotLogEntries.unshift(entry);
    if (plotLogEntries.length > 75) {
        plotLogEntries = plotLogEntries.slice(0, 75);
    }
    updatePlotLogUi();
}

function updatePlotLogEntry(entryId, patch) {
    if (!entryId) {
        return;
    }

    const entryIndex = plotLogEntries.findIndex((entry) => entry.clientLogId === entryId);
    if (entryIndex < 0) {
        return;
    }

    plotLogEntries[entryIndex] = {
        ...plotLogEntries[entryIndex],
        ...patch,
    };
    updatePlotLogUi();
}

function normalizePersistedLogEntry(entry) {
    const durationValue = Number(entry?.duration);
    const pathValue = Number(entry?.path);
    const travelValue = Number(entry?.travel);

    return {
        ...entry,
        duration: Number.isFinite(durationValue) ? formatDurationClock(durationValue) : (entry?.duration || '-'),
        path: Number.isFinite(pathValue) ? pathValue.toFixed(2) : (entry?.path || '-'),
        travel: Number.isFinite(travelValue) ? travelValue.toFixed(2) : (entry?.travel || '-'),
    };
}

async function loadPersistedPlotLog() {
    try {
        const response = await fetch('/logs.json', { cache: 'no-store' });
        if (!response.ok) {
            return;
        }

        const payload = await response.json();
        const entries = Array.isArray(payload?.entries) ? payload.entries : [];
        plotLogEntries = entries.slice(0, 75).map(normalizePersistedLogEntry);
        updatePlotLogUi();
    } catch (error) {
        console.error('Failed to load persisted plot log:', error);
    }
}

function initializePlotLog() {
    const clearButton = document.querySelector('#clear-plot-log');
    if (clearButton) {
        clearButton.addEventListener('click', async function() {
            const confirmed = window.confirm('Clear all plot log entries? This cannot be undone.');
            if (!confirmed) {
                return;
            }

            try {
                await fetch('/logs.json', { method: 'DELETE' });
            } catch (error) {
                console.error('Failed to clear persisted plot log:', error);
            }

            plotLogEntries = [];
            updatePlotLogUi();
        });
    }

    loadPersistedPlotLog();
}

function formatCountdown(totalSeconds) {
    const safeSeconds = Math.max(0, Math.floor(totalSeconds));
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const seconds = safeSeconds % 60;

    return [hours, minutes, seconds]
        .map((part) => String(part).padStart(2, '0'))
        .join(':');
}

function formatCompactDuration(totalSeconds) {
    if (!Number.isFinite(totalSeconds) || totalSeconds <= 0) {
        return null;
    }

    if (totalSeconds >= 3600) {
        const hoursValue = totalSeconds / 3600;
        const roundedHours = Number(hoursValue.toFixed(1));
        return `${roundedHours}h`;
    }

    if (totalSeconds >= 60) {
        return `${Math.ceil(totalSeconds / 60)}m`;
    }

    return `${Math.ceil(totalSeconds)}s`;
}

function getBusyDurationSeconds() {
    if (plotCountdownEndTimeMs) {
        return Math.max(0, Math.ceil((plotCountdownEndTimeMs - Date.now()) / 1000));
    }

    const estimatedDuration = Number(currentPreviewEstimate?.plot_duration);
    if (Number.isFinite(estimatedDuration) && estimatedDuration > 0) {
        return Math.ceil(estimatedDuration);
    }

    return null;
}

function buildStatusChipText(statusText, rawStatus) {
    if (rawStatus !== 'busy') {
        return statusText;
    }

    const compactDuration = formatCompactDuration(getBusyDurationSeconds());
    return compactDuration ? `${statusText} ${compactDuration}` : statusText;
}

function setCountdownValue(value, isActive = false) {
    const countdownElement = document.querySelector('#preview-countdown');
    if (!countdownElement) {
        return;
    }

    countdownElement.textContent = value;
    countdownElement.classList.toggle('is-active', isActive);

    if (currentPlotterData?.status === 'busy') {
        const statusChip = document.querySelector('#plotter-status-chip');
        if (statusChip) {
            statusChip.textContent = buildStatusChipText('Busy', 'busy');
        }
    }
}

function stopPlotCountdown(resetToZero = false) {
    if (plotCountdownInterval) {
        clearInterval(plotCountdownInterval);
        plotCountdownInterval = null;
    }

    plotCountdownEndTimeMs = null;

    if (resetToZero) {
        setCountdownValue('00:00:00', false);
    } else {
        const currentValue = document.querySelector('#preview-countdown')?.textContent || '00:00:00';
        setCountdownValue(currentValue, false);
    }
}

function startPlotCountdown(totalSeconds) {
    const numericSeconds = Number(totalSeconds);

    if (!Number.isFinite(numericSeconds) || numericSeconds <= 0) {
        stopPlotCountdown(true);
        return;
    }

    stopPlotCountdown();
    plotCountdownEndTimeMs = Date.now() + (numericSeconds * 1000);
    setCountdownValue(formatCountdown(numericSeconds), true);

    plotCountdownInterval = setInterval(() => {
        const remainingSeconds = Math.ceil((plotCountdownEndTimeMs - Date.now()) / 1000);
        setCountdownValue(formatCountdown(remainingSeconds), true);

        if (remainingSeconds <= 0) {
            clearInterval(plotCountdownInterval);
            plotCountdownInterval = null;
            setCountdownValue('00:00:00', true);
        }
    }, 250);
}

function resetPreviewEstimate(message = 'Loading') {
    currentPreviewEstimate = null;
    const durationElement = document.querySelector('#preview-duration');
    durationElement.textContent = message;
    durationElement.className = 'preview-metric__value';
    stopPlotCountdown(true);
    setText('#preview-path', message);
    setText('#preview-travel', message);
}

function renderPreviewEstimate(data) {
    currentPreviewEstimate = data;
    const estimatedDurationSeconds = Number(data.plot_duration);
    const durationElement = document.querySelector('#preview-duration');
    durationElement.textContent = formatDurationFromSeconds(estimatedDurationSeconds);
    durationElement.className = 'preview-metric__value';
    setCountdownValue(formatCountdown(estimatedDurationSeconds), false);
    setText('#preview-path', formatMeters(Number(data.plot_path)));
    setText('#preview-travel', formatMeters(Number(data.plot_travel)));
}

async function loadPreviewEstimate(filename, loadRequestId, selectedLayerValue = '') {
    resetPreviewEstimate('Loading');
    document.querySelector('#preview-duration').className = 'preview-metric__value info-tile__value--pending';
    document.querySelector('#preview-path').className = 'preview-metric__value info-tile__value--pending';
    document.querySelector('#preview-travel').className = 'preview-metric__value info-tile__value--pending';

    try {
        const response = await fetch(buildPlotRequestPath(filename, buildPreviewRequestQuery(selectedLayerValue)));
        if (loadRequestId !== previewLoadRequestId) {
            return;
        }

        if (!response.ok) {
            throw new Error(`Preview request failed with status ${response.status}`);
        }

        const previewData = await response.json();
        if (loadRequestId !== previewLoadRequestId) {
            return;
        }

        renderPreviewEstimate(previewData);
    } catch (error) {
        console.error('Failed to load preview estimate:', error);
        if (loadRequestId !== previewLoadRequestId) {
            return;
        }

        resetPreviewEstimate('Unavailable');
    }
}

function initializePlaybackControls() {
    const speedSlider = document.querySelector('#speed-slider');

    document.querySelector('#play-pause-btn').addEventListener('click', async function() {
        if (!currentAnimator) {
            return;
        }

        if (isPlaybackActive) {
            currentAnimator.pause();
            setPlaybackButtonState(false);
        } else {
            try {
                if (!currentAnimator.animationPrepared || currentAnimator.currentPathIndex >= currentAnimator.paths.length) {
                    setPreviewLoading(true, 'Preparing animation...');
                    await waitForBrowserFrame();
                }

                await currentAnimator.play();
                setPlaybackButtonState(true);
            } finally {
                setPreviewLoading(false);
            }
        }
    });

    document.querySelector('#reset-btn').addEventListener('click', function() {
        if (!currentAnimator) {
            return;
        }

        currentAnimator.reset();
        updatePlaybackProgress({ current: 0, total: currentAnimator.paths.length, percentage: 0 });
        setPlaybackButtonState(false);
    });

    speedSlider.addEventListener('input', function(event) {
        const value = Number(event.target.value);
        setText('#speed-display', `${value} ms`);

        if (currentAnimator) {
            currentAnimator.setSpeed(value);
        }
    });
}

function formatInches(valueInMillimeters) {
    const valueInInches = valueInMillimeters / 25.4;
    const fixed = valueInInches.toFixed(1);
    return fixed.endsWith('.0') ? fixed.slice(0, -2) : fixed;
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem('plot-server-theme', theme);

    const label = document.querySelector('#theme-toggle-label');
    const button = document.querySelector('#theme-toggle');
    const themeMeta = document.querySelector('#theme-color-meta');
    const isDark = theme === 'dark';

    if (label) {
        label.textContent = isDark ? 'Light mode' : 'Dark mode';
    }

    if (button) {
        button.setAttribute('aria-pressed', isDark ? 'true' : 'false');
    }

    if (themeMeta) {
        themeMeta.setAttribute('content', isDark ? '#13110f' : '#d8d1c7');
    }
}

function initializeThemeToggle() {
    const currentTheme = document.documentElement.dataset.theme || 'light';
    applyTheme(currentTheme);

    document.querySelector('#theme-toggle').addEventListener('click', function() {
        const nextTheme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        applyTheme(nextTheme);
    });
}

function setUploadStatus(message, isError = false) {
    const statusElement = document.querySelector('#upload-status');
    statusElement.textContent = message;
    statusElement.className = isError ? 'upload-status upload-status-error' : 'upload-status';
}

function validateUploadFile(file) {
    if (!file) {
        setUploadStatus('No file selected', true);
        return false;
    }

    const isSvgType = ['image/svg+xml', 'text/xml', 'application/xml'].includes(file.type);
    const isSvgExtension = file.name.toLowerCase().endsWith('.svg');

    if (!isSvgType && !isSvgExtension) {
        setUploadStatus('Please upload an SVG file', true);
        return false;
    }

    if (file.size > 5 * 1024 * 1024) {
        setUploadStatus('SVG must be smaller than 5MB', true);
        return false;
    }

    return true;
}

function uploadSvgFile(file) {
    if (!validateUploadFile(file)) {
        return;
    }

    setUploadStatus(`Uploading ${file.name}...`);

    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/plot/upload');
    xhr.onload = function() {
        if (xhr.status === 200) {
            setUploadStatus('Upload complete');
            const url = new URL(window.location);
            url.searchParams.set('plot', file.name);
            window.location.href = url.toString();
        } else {
            const message = xhr.responseText || 'Upload failed';
            setUploadStatus(message, true);
        }
    };
    xhr.onerror = function() {
        setUploadStatus('Upload failed', true);
    };
    xhr.send(formData);
}

function initializeUploadDropZone() {
    const dropZone = document.querySelector('#drop-zone');
    const fileInput = document.querySelector('#file');
    const browseLink = document.querySelector('#browse-link');

    const openFileDialog = () => fileInput.click();

    browseLink.addEventListener('click', function(event) {
        event.preventDefault();
        openFileDialog();
    });

    dropZone.addEventListener('click', function(event) {
        if (event.target.closest('#browse-link')) {
            return;
        }
        openFileDialog();
    });

    dropZone.addEventListener('keydown', function(event) {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            openFileDialog();
        }
    });

    ['dragenter', 'dragover'].forEach((eventName) => {
        dropZone.addEventListener(eventName, function(event) {
            event.preventDefault();
            dropZone.classList.add('drag-over');
        });
    });

    ['dragleave', 'dragend'].forEach((eventName) => {
        dropZone.addEventListener(eventName, function(event) {
            event.preventDefault();
            if (!dropZone.contains(event.relatedTarget)) {
                dropZone.classList.remove('drag-over');
            }
        });
    });

    dropZone.addEventListener('drop', function(event) {
        event.preventDefault();
        dropZone.classList.remove('drag-over');
        const [file] = event.dataTransfer.files;
        uploadSvgFile(file);
    });

    fileInput.addEventListener('change', function(event) {
        const [file] = event.target.files;
        uploadSvgFile(file);
        event.target.value = '';
    });
}

async function copyTextToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return;
    }

    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.setAttribute('readonly', '');
    textArea.style.position = 'absolute';
    textArea.style.left = '-9999px';
    document.body.appendChild(textArea);
    textArea.select();
    document.execCommand('copy');
    document.body.removeChild(textArea);
}

let copyPlotCommandTimeout = null;
let plotterControlStatusTimeout = null;

function showCopyPlotCommandStatus(message, isError = false) {
    const statusElement = document.querySelector('#copy-plot-command-status');
    statusElement.textContent = message;
    statusElement.className = isError ? 'copy-status copy-status-error' : 'copy-status';

    if (copyPlotCommandTimeout) {
        clearTimeout(copyPlotCommandTimeout);
    }

    copyPlotCommandTimeout = setTimeout(() => {
        statusElement.textContent = '';
        statusElement.className = '';
    }, 2000);
}

function showToggleServoStatus(message, isError = false) {
    const statusElement = document.querySelector('#plotter-control-status');
    if (!statusElement) {
        return;
    }

    statusElement.textContent = message;
    statusElement.className = isError ? 'plotter-control-status copy-status copy-status-error' : 'plotter-control-status copy-status';

    if (plotterControlStatusTimeout) {
        clearTimeout(plotterControlStatusTimeout);
    }

    plotterControlStatusTimeout = setTimeout(() => {
        statusElement.textContent = '';
        statusElement.className = 'plotter-control-status';
    }, 2500);
}

function showStopPlotStatus(message, isError = false) {
    showToggleServoStatus(message, isError);
}

async function toggleServo() {
    const toggleButton = document.querySelector('#toggle-servo-button');
    if (!toggleButton || toggleButton.disabled) {
        return;
    }

    servoRequestInFlight = true;
    setBusyStatusLocally();
    syncControlButtons();
    showToggleServoStatus('Toggling...');

    try {
        const response = await fetch('/servo/toggle', { method: 'POST' });
        if (response.status === 503) {
            showToggleServoStatus('Plotter busy', true);
            return;
        }

        if (!response.ok) {
            let errorMessage = `Servo toggle request failed (${response.status})`;
            try {
                const errorPayload = await response.json();
                if (errorPayload && errorPayload.error) {
                    errorMessage = errorPayload.error;
                }
            } catch (parseError) {
                // Keep fallback message when response is not JSON.
            }
            throw new Error(errorMessage);
        }

        showToggleServoStatus('Servo toggled');

        await refreshPlotterStatus();
    } catch (error) {
        console.error('Failed to toggle servo:', error);
        showToggleServoStatus(error.message || 'Toggle failed', true);
    } finally {
        servoRequestInFlight = false;
        await refreshPlotterStatus();
    }
}

async function stopPlot() {
    const stopButton = document.querySelector('#stop-plot-button');
    if (!stopButton || stopButton.disabled) {
        return;
    }

    stopRequestInFlight = true;
    preserveCountdownAfterStop = true;
    stopPlotCountdown(false);
    syncControlButtons();
    showStopPlotStatus('Stopping...');
    addPlotLogEntry({
        time: formatCompactTimestamp(),
        status: 'stopping',
        ...getCurrentPlotContext(),
    });

    try {
        const response = await fetch('/plot/stop', { method: 'POST' });

        if (response.status === 409) {
            showStopPlotStatus('No active plot', true);
            return;
        }

        if (!response.ok) {
            let errorMessage = `Stop request failed (${response.status})`;
            try {
                const errorPayload = await response.json();
                if (errorPayload && errorPayload.error) {
                    errorMessage = errorPayload.error;
                }
            } catch (parseError) {
                // Keep fallback error message when payload is not JSON.
            }
            throw new Error(errorMessage);
        }

        const payload = await response.json();
        const servoState = payload?.stop_result?.servo_state || 'unknown';
        if (servoState === 'commanded_up') {
            showStopPlotStatus('Stop sent, pen up + motors off');
        } else {
            showStopPlotStatus('Stop sent', false);
        }

        addPlotLogEntry({
            time: formatCompactTimestamp(payload?.stop_result?.requested_at),
            status: 'stopped',
            ...getCurrentPlotContext(),
        });

        await refreshPlotterStatus();
    } catch (error) {
        console.error('Failed to stop active plot:', error);
        showStopPlotStatus(error.message || 'Stop failed', true);
        addPlotLogEntry({
            time: formatCompactTimestamp(),
            status: 'error',
            ...getCurrentPlotContext(),
        });
    } finally {
        stopRequestInFlight = false;
        await refreshPlotterStatus();
    }
}

// Check plotter status with detailed information
var xhr = new XMLHttpRequest();
xhr.open('GET', '/status.json');
xhr.onload = function() {
    if (xhr.status === 200) {
        let plotterData = JSON.parse(xhr.responseText);

        // Update status display
        updatePlotterStatus(plotterData);

        syncControlButtons();
    } else {
        console.log("Error: " + xhr.status);
        setText("#plotter-status-chip", "Error");
        syncControlButtons();
    }
};
xhr.send();

// Function to update plotter status display
function updatePlotterStatus(data) {
    // Store plotter data globally
    currentPlotterData = data;

    // Show status with color coding
    let statusText = data.status;
    let color = "black";
    if (data.status === 'busy') {
        statusText = 'Busy';
        color = '#7f3fbf'; // purple
        // Start polling if not already polling
        if (!busyPollingInterval) {
            busyPollingInterval = setInterval(() => {
                fetch('/status.json')
                    .then(res => res.json())
                    .then(newStatus => {
                        if (newStatus.status !== 'busy') {
                            clearInterval(busyPollingInterval);
                            busyPollingInterval = null;
                        }
                        updatePlotterStatus(newStatus);
                    });
            }, 5000);
        }
    } else if (data.status === 'connected') {
        color = '#ff6600'; // orange
        statusText = 'Connected (Check Power)';
    } else if (data.status === 'on') {
        statusText = 'Ready';
        color = '#2ecc40'; // green
        // Stop polling if plotter is ready
        if (busyPollingInterval) {
            clearInterval(busyPollingInterval);
            busyPollingInterval = null;
        }
    } else if (data.status === 'off') {
        statusText = 'Disconnected';
        color = '#ff4136'; // red
        // Stop polling if plotter is off
        if (busyPollingInterval) {
            clearInterval(busyPollingInterval);
            busyPollingInterval = null;
        }
    }

    setText("#plotter-status-chip", buildStatusChipText(statusText, data.status));
    document.querySelector("#plotter-status-chip").style.color = color;

    // Machine information
    setText("#plotter-machine-chip", data.machine || "Unknown");

    // Check SVG fit if we have SVG dimensions
    checkSvgFit();
    syncControlButtons();
}

// Function to extract SVG dimensions
function extractSvgDimensions(svgDocument) {
    const svgElement = svgDocument?.tagName === 'svg' ? svgDocument : svgDocument?.querySelector('svg');

    if (!svgElement) {
        return null;
    }

    let width, height;

    // Try to get dimensions from viewBox first
    const viewBox = svgElement.getAttribute('viewBox');
    if (viewBox) {
        const [x, y, w, h] = viewBox.split(' ').map(parseFloat);
        // ViewBox dimensions are in SVG user units (pixels at 96 DPI)
        width = w * (25.4 / 96); // Convert to mm
        height = h * (25.4 / 96);
    } else {
        // Fall back to width/height attributes
        const widthAttr = svgElement.getAttribute('width');
        const heightAttr = svgElement.getAttribute('height');

        if (widthAttr && heightAttr) {
            // Remove units and convert to numbers
            width = parseFloat(widthAttr.replace(/[^\d.]/g, ''));
            height = parseFloat(heightAttr.replace(/[^\d.]/g, ''));

            // Convert from other units to mm if needed
            // SVG uses 96 DPI standard (1 inch = 96 pixels, 1 inch = 25.4 mm)
            if (widthAttr.includes('px') || !isNaN(parseFloat(widthAttr))) {
                width = width * (25.4 / 96); // Convert px to mm at 96 DPI
                height = height * (25.4 / 96);
            } else if (widthAttr.includes('in')) {
                width = width * 25.4; // Convert inches to mm
                height = height * 25.4;
            }
            // Assume mm if no conversion needed
        }
    }

    return width && height ? { width, height } : null;
}

// Function to check if SVG fits within plotter bounds
function checkSvgFit() {
    const fits = doesCurrentSvgFitPlotter();
    if (fits === null) {
        const fitElement = document.querySelector("#plotter-fit");
        fitElement.textContent = "Plotter Fit: NA";
        fitElement.className = "";
        return;
    }

    const svgWidth = currentSvgDimensions.width;
    const svgHeight = currentSvgDimensions.height;
    const plotterWidth = currentPlotterData.config.x_travel * 25.4;
    const plotterHeight = currentPlotterData.config.y_travel * 25.4;
    const fitsWidth = svgWidth <= plotterWidth;
    const fitsHeight = svgHeight <= plotterHeight;

    let fitText = "";
    let fitClass = "";

    if (fits) {
        fitText = "Plotter Fit: Yes";
        fitClass = "fit-good";
    } else {
        const issues = [];
        if (!fitsWidth) issues.push(`width: ${svgWidth.toFixed(1)}mm > ${plotterWidth.toFixed(1)}mm`);
        if (!fitsHeight) issues.push(`height: ${svgHeight.toFixed(1)}mm > ${plotterHeight.toFixed(1)}mm`);
        fitText = `Fit: No, ${issues.join(', ')}`;
        fitClass = "fit-bad";
    }

    const fitElement = document.querySelector("#plotter-fit");
    fitElement.textContent = fitText;
    fitElement.className = fitClass;
}

function analyzeLoadedSvg(filename, availableLayers = null, selectedLayerValue = '') {
    const svgElement = getCurrentSvgElement();

    if (!svgElement) {
        currentSvgDimensions = null;
        document.querySelector("#svg-dimensions").textContent = "Unable to determine";
        const fitElement = document.querySelector("#plotter-fit");
        fitElement.textContent = "Fit: -";
        fitElement.className = "";
        return;
    }

    const dimensions = extractSvgDimensions(svgElement);
    if (dimensions) {
        currentSvgDimensions = dimensions;
        document.querySelector("#svg-dimensions").textContent =
            `${dimensions.width.toFixed(1)}mm × ${dimensions.height.toFixed(1)}mm (${formatInches(dimensions.width)}" x ${formatInches(dimensions.height)}")`;
        checkSvgFit();
    } else {
        currentSvgDimensions = null;
        document.querySelector("#svg-dimensions").textContent = "Unable to determine";
        const fitElement = document.querySelector("#plotter-fit");
        fitElement.textContent = "Fit: -";
        fitElement.className = "";
    }

    const layers = availableLayers || extractPlottableLayers(svgElement);

    document.querySelector("form[name=plot] input[name=filename]").value = filename;

    let select_menu = document.querySelector("select[name=layer]");
    var length = select_menu.options.length;
    for (let i = length - 1; i >= 0; i--) {
        select_menu.options[i] = null;
    }

    let opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "All";
    select_menu.appendChild(opt);
    layers.forEach((item) => {
        let opt = document.createElement("option");
        opt.value = String(item.number);
        opt.textContent = item.label;
        select_menu.appendChild(opt);
    });

    const hasSelectedLayer = selectedLayerValue && layers.some((item) => String(item.number) === selectedLayerValue);
    select_menu.value = hasSelectedLayer ? selectedLayerValue : '';

    updatePlotCommand();
    syncControlButtons();
}

async function loadAnimatedPreview(filepath, filename, selectedLayerValue = '') {
    const previewElement = document.querySelector('#svg-object');
    const loadRequestId = ++previewLoadRequestId;
    previewElement.setAttribute('data-filename', filename);
    resetPreviewEstimate();
    if (currentAnimator) {
        currentAnimator.pause();
    }
    revokeCurrentPreviewObjectUrl();
    setPlaybackControlsEnabled(false);
    setPlaybackButtonState(false);
    updatePlaybackProgress({ current: 0, total: 0, percentage: 0 });
    setPreviewLoading(true, 'Loading SVG...');
    loadPreviewEstimate(filename, loadRequestId, selectedLayerValue);
    await waitForBrowserFrame();

    try {
        const svgResponse = await fetch(filepath);
        if (!svgResponse.ok) {
            throw new Error(`SVG request failed with status ${svgResponse.status}`);
        }

        const svgMarkup = await svgResponse.text();
        const previewPayload = buildPreviewSvgPayload(svgMarkup, selectedLayerValue);
        currentPreviewObjectUrl = URL.createObjectURL(new Blob([previewPayload.previewSvgMarkup], { type: 'image/svg+xml' }));
        setSvgSourceText(previewPayload.previewSvgMarkup);

        const SimplePlotAnimatorClass = getSimplePlotAnimatorClass();
        if (!SimplePlotAnimatorClass) {
            throw new Error('SimplePlotAnimator UMD bundle is not available');
        }

        if (!currentAnimator) {
            currentAnimator = new SimplePlotAnimatorClass(previewElement, {
                speed: Number(document.querySelector('#speed-slider').value),
                onProgress: updatePlaybackProgress,
                onComplete: () => {
                    updatePlaybackProgress({ current: currentAnimator.paths.length, total: currentAnimator.paths.length, percentage: 100 });
                    setPlaybackButtonState(false);
                }
            });
        } else {
            currentAnimator.pause();
            currentAnimator.onProgress = updatePlaybackProgress;
            currentAnimator.onComplete = () => {
                updatePlaybackProgress({ current: currentAnimator.paths.length, total: currentAnimator.paths.length, percentage: 100 });
                setPlaybackButtonState(false);
            };
            currentAnimator.setSpeed(Number(document.querySelector('#speed-slider').value));
        }

        setPreviewLoading(true, 'Rendering preview...');
        const loaded = await currentAnimator.loadFromURL(currentPreviewObjectUrl);
        if (!loaded || loadRequestId !== previewLoadRequestId) {
            return;
        }

        setPreviewLoading(true, 'Analyzing layers...');
        await waitForBrowserFrame();
        analyzeLoadedSvg(filename, previewPayload.availableLayers, selectedLayerValue);
        setPlaybackControlsEnabled(true);
        updatePlaybackProgress({ current: 0, total: currentAnimator.paths.length, percentage: 0 });
        setPlaybackButtonState(false);
    } catch (error) {
        console.error('Failed to load animated preview:', error);
        previewElement.innerHTML = '<div class="plot-animator-error">Error loading preview</div>';
        setSvgSourceText('');
    } finally {
        if (loadRequestId === previewLoadRequestId) {
            setPreviewLoading(false);
        }
    }
}

function markThumbnailUnavailable(imageElement) {
    const fileLink = imageElement.closest('.file-link');
    if (fileLink) {
        fileLink.classList.add('file-link--no-thumbnail');
    }
}

function initializeFileThumbnails() {
    document.querySelectorAll('.file-thumb').forEach((imageElement) => {
        imageElement.addEventListener('error', function() {
            markThumbnailUnavailable(imageElement);
        });

        if (imageElement.complete && imageElement.naturalWidth === 0) {
            markThumbnailUnavailable(imageElement);
        }
    });
}

document.querySelectorAll("#files a").forEach(item => {
    item.addEventListener("click", async function(event){
        event.preventDefault();

        // Clear any "selected" list items
        document.querySelectorAll('ol#files li.selected').forEach((el) => el.classList.remove('selected'));

        // Apply class to highlight clicked item
        const selectedListItem = event.currentTarget.closest('li');
        selectedListItem.classList.add('selected');
        scrollLibraryItemIntoView(selectedListItem);

        // Parse file request
        let filepath = this.getAttribute("href");
        let filename = this.getAttribute("data-filename");

        setSelectedPlot(filename);

        // Update the URL with ?plot={plotname} (no reload)
        const url = new URL(window.location);
        url.searchParams.set('plot', filename);
        url.searchParams.delete('layer');
        window.history.replaceState({}, '', url);

        await loadAnimatedPreview(filepath, filename, '');
    });
});

function updatePlotCommand() {

    const svg = document.getElementById('svg-object');

    const filename = svg.getAttribute('data-filename') || "";
    const title = document.querySelector("input[name=title]").value || "";
    const tool = document.querySelector("select[name=tool]").value;
    const material = document.querySelector("select[name=material]").value;
    const layer = document.querySelector("select[name=layer]").value || "all";

    let command = `plot -f "${document.body.dataset.artDir || ""}/${filename}"`;
    if (title) {
        command += ` -t "${title}"`;
    }

    const editions = 1;
    const edition_number = 1;

    command += ` -e ${editions} -x ${edition_number} -i "${tool}" -p "${material}"`;

    if (layer !== "all") {
        command += ` -y "${layer}"`;
    }

    const svgElement = getCurrentSvgElement();
    const dimensions = extractSvgDimensions(svgElement);
    if (dimensions) {

        // Option 1: Use mm returned by extractSvgDimensions
        // command += ` -l "${dimensions.width.toFixed(1)}×${dimensions.height.toFixed(1)}"`;

        // Option 2: Convert from mm to inches
        const width_inches = dimensions.width / 25.4;
        const height_inches = dimensions.height / 25.4;

        // Store dimensions with trimmed format (remove ".0" for whole numbers)
        const formatDimension = (value) => {
            const fixed = value.toFixed(1);
            return fixed.endsWith('.0') ? fixed.slice(0, -2) : fixed;
        };
        command += ` -l "${formatDimension(width_inches)}×${formatDimension(height_inches)}"`;

        if (dimensions.width > dimensions.height) {
            command += ` -o "Landscape"`;
        } else {
            command += ` -o "Portrait"`;
        }
    }

    document.querySelector("#plot-command").textContent = command;
}

// Call updatePlotCommand whenever a plot option changes
document.querySelectorAll('form[name=plot] input, form[name=plot] select').forEach(elem => {
    elem.addEventListener("change", updatePlotCommand);
});

document.querySelector('select[name=layer]').addEventListener('change', function(event) {
    const filename = document.querySelector("form[name=plot] input[name=filename]").value;
    if (!filename) {
        return;
    }

    updateLayerQueryString(event.target.value);
    loadAnimatedPreview(buildSvgAssetPath(filename), filename, event.target.value);
});

document.querySelector('#copy-plot-command').addEventListener('click', async function() {
    const command = document.querySelector('#plot-command').textContent.trim();

    if (!command) {
        showCopyPlotCommandStatus('Nothing to copy', true);
        return;
    }

    try {
        await copyTextToClipboard(command);
        showCopyPlotCommandStatus('Copied');
    } catch (error) {
        console.error('Failed to copy plot command:', error);
        showCopyPlotCommandStatus('Copy failed', true);
    }
});

['#download-svg-link', '#download-pdf-link'].forEach((selector) => {
    document.querySelector(selector).addEventListener('click', function(event) {
        if (event.currentTarget.getAttribute('aria-disabled') === 'true') {
            event.preventDefault();
        }
    });
});

document.querySelector('#delete-file-button').addEventListener('click', function() {
    deleteSelectedFile();
});

document.querySelector('#toggle-servo-button').addEventListener('click', function() {
    toggleServo();
});

document.querySelector('#stop-plot-button').addEventListener('click', function() {
    stopPlot();
});

// Add event handler to Plot button
document.querySelector('form[name=plot]').addEventListener("submit", function(event){

    // Get plot filename
    let filename = document.querySelector("form[name=plot] input[name=filename]").value;

    // Get selected layer. Set to null if "all" layers (empty value) should be plotted
    let layer = document.querySelector("select[name=layer]").value;
    if (layer == "") {
        layer = null;
    }

    // Set plot request
    send_plot_request(filename, layer)
        .catch((error) => {
            console.error('Failed to start plot request:', error);
            window.alert(error.message || 'Failed to start plot');
        });

    event.preventDefault();
});

// On page load, check for ?plot= param and load that plot, else load the first
window.addEventListener("load", function() {
    initializeThemeToggle();
    initializeUploadDropZone();
    initializePlaybackControls();
    initializeInfoModal();
    initializeFileThumbnails();
    initializePlotLog();
    const urlParams = new URLSearchParams(window.location.search);
    let plotParam = urlParams.get('plot');
    let layerParam = urlParams.get('layer') || '';
    let found = false;
    if (plotParam) {
        // Try to find the matching plot in the list
        let plotLink = Array.from(document.querySelectorAll('ol#files li a')).find(a => a.getAttribute('data-filename') === plotParam);
        if (plotLink) {
            let filename = plotLink.getAttribute('data-filename');
            let filepath = buildSvgAssetPath(filename);
            setSelectedPlot(filename);
            loadAnimatedPreview(filepath, filename, layerParam);
            // Highlight the selected item
            plotLink.parentElement.classList.add('selected');
            scrollLibraryItemIntoView(plotLink.parentElement);
            found = true;
        }
    }
    if (!found) {
        // Fallback: load the first plot
        let first_plot = document.querySelector('ol#files li a');
        if (first_plot) {
            let filename = first_plot.getAttribute('data-filename');
            let filepath = buildSvgAssetPath(filename);
            setSelectedPlot(filename);
            loadAnimatedPreview(filepath, filename, layerParam);
            first_plot.parentElement.classList.add('selected');
            scrollLibraryItemIntoView(first_plot.parentElement);
        }
    }
    updatePlotCommand();
});

document.querySelector('#library-search').addEventListener('input', function(event) {
    const query = event.target.value.trim().toLowerCase();
    document.querySelectorAll('#files li').forEach((item) => {
        const filename = item.getAttribute('data-filename').toLowerCase();
        item.style.display = filename.includes(query) ? '' : 'none';
    });
});

// Send an API request to start a plot
function send_plot_request(filename, layer = null){
    const context = getCurrentPlotContext(filename, layer);
    const clientLogId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    activePlotLogEntryId = clientLogId;
    const requestParams = new URLSearchParams();
    if (layer != null) {
        requestParams.set('layer', String(layer));
    }

    requestParams.set('title', context.title || '');
    requestParams.set('tool', context.tool || 'None');
    requestParams.set('media', context.media || 'None');
    requestParams.set('format', context.format || '');
    requestParams.set('orientation', context.orientation || '');
    requestParams.set('edition', String(context.edition || 1));
    requestParams.set('editions', String(context.editions || 1));

    const requestQuery = requestParams.toString();
    const request = requestQuery
        ? `${buildPlotRequestPath(filename)}?${requestQuery}`
        : buildPlotRequestPath(filename);

    preserveCountdownAfterStop = false;
    plotRequestInFlight = true;
    startPlotCountdown(currentPreviewEstimate?.plot_duration);
    setBusyStatusLocally();
    syncControlButtons();

    addPlotLogEntry({
        clientLogId,
        time: formatCompactTimestamp(Math.floor(Date.now() / 1000)),
        status: 'plotting',
        ...context,
        duration: '-',
        path: '-',
        travel: '-',
        lifts: '-',
    });

    return fetch(request, { cache: 'no-store' })
        .then(async (response) => {
            if (response.status === 503) {
                throw new Error('Plotter is busy');
            }

            if (!response.ok) {
                const details = await response.text();
                throw new Error(details || `Plot request failed (${response.status})`);
            }

            const contentType = response.headers.get('content-type') || '';
            if (contentType.includes('application/json')) {
                const payload = await response.json();
                const metrics = payload?.metrics || {};
                updatePlotLogEntry(clientLogId, {
                    status: 'ok',
                    title: payload?.title || context.title,
                    filename: payload?.filename || context.filename,
                    fileHash: payload?.file_hash || '-',
                    plotter: payload?.plotter || context.plotter,
                    edition: `${payload?.edition || context.edition || 1}/${payload?.editions || context.editions || 1}`,
                    layer: payload?.layer > 0 ? String(payload.layer) : context.layer,
                    tool: payload?.tool || context.tool,
                    media: payload?.media || context.media,
                    format: payload?.format || context.format,
                    orientation: payload?.orientation || context.orientation,
                    duration: formatDurationClock(metrics.plot_duration),
                    path: Number.isFinite(Number(metrics.plot_path)) ? Number(metrics.plot_path).toFixed(2) : '-',
                    travel: Number.isFinite(Number(metrics.plot_travel)) ? Number(metrics.plot_travel).toFixed(2) : '-',
                    lifts: Number.isFinite(Number(metrics.lifts)) ? String(metrics.lifts) : '-',
                });
                return payload;
            }

            const message = await response.text();
            updatePlotLogEntry(clientLogId, {
                status: 'ok',
                ...getCurrentPlotContext(filename, layer),
            });
            console.log(message);
            return message;
        })
        .catch((error) => {
            updatePlotLogEntry(clientLogId, {
                status: 'error',
                ...getCurrentPlotContext(filename, layer),
            });
            throw error;
        })
        .finally(async () => {
            if (activePlotLogEntryId === clientLogId) {
                activePlotLogEntryId = null;
            }
            plotRequestInFlight = false;
            const shouldPreserveCountdown = preserveCountdownAfterStop;
            preserveCountdownAfterStop = false;
            stopPlotCountdown(!shouldPreserveCountdown);
            await refreshPlotterStatus();
        });
}