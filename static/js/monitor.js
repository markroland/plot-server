
(() => {
    const storedTheme = localStorage.getItem('plot-server-theme');
    const preferredTheme = storedTheme || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = preferredTheme;
})();

const PLOTTING_POLL_MS = 5000;
const IDLE_POLL_MS = 15000;
const FETCH_TIMEOUT_MS = 8000;

let latestStatus = null;
let latestStatusFetchedAtMs = 0;
let connectionLost = false;
let pollTimeout = null;
let shownThumbnailUrl = '';
let failedThumbnailUrl = '';

function byId(id) {
    return document.getElementById(id);
}

function formatClock(totalSeconds) {
    const safeSeconds = Math.max(0, Math.floor(totalSeconds));
    const hours = Math.floor(safeSeconds / 3600);
    const minutes = Math.floor((safeSeconds % 3600) / 60);
    const seconds = safeSeconds % 60;
    const paddedSeconds = String(seconds).padStart(2, '0');

    if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, '0')}:${paddedSeconds}`;
    }

    return `${String(minutes).padStart(2, '0')}:${paddedSeconds}`;
}

// Elapsed time is measured from the server's clock at fetch time and advanced
// locally, so a phone with a wrong clock still shows correct progress.
function getPlotProgress(nowMs) {
    const job = latestStatus?.current_plot;
    if (!job) {
        return null;
    }

    const elapsedAtFetch = Number(latestStatus.server_time) - Number(job.started_at);
    const elapsedSeconds = Math.max(0, (Number.isFinite(elapsedAtFetch) ? elapsedAtFetch : 0) + (nowMs - latestStatusFetchedAtMs) / 1000);
    const estimatedSeconds = Number(job.estimate?.plot_duration);
    const hasEstimate = Number.isFinite(estimatedSeconds) && estimatedSeconds > 0;

    return {
        job,
        elapsedSeconds,
        estimatedSeconds: hasEstimate ? estimatedSeconds : null,
        remainingSeconds: hasEstimate ? Math.max(0, estimatedSeconds - elapsedSeconds) : null,
        fraction: hasEstimate ? Math.min(1, elapsedSeconds / estimatedSeconds) : null,
        isOverEstimate: hasEstimate && elapsedSeconds > estimatedSeconds,
    };
}

function describeStatus(progress) {
    const status = latestStatus?.status;

    if (!latestStatus) {
        return { state: 'loading', text: 'Checking...', note: '' };
    }

    if (status === 'busy') {
        if (progress || latestStatus.plot_state === 'plotting') {
            const note = progress ? '' : 'Plot details are not available.';
            return { state: 'plotting', text: 'Plotting', note };
        }
        return { state: 'plotting', text: 'Busy', note: 'The plotter is busy with another task.' };
    }

    if (status === 'on') {
        return { state: 'ready', text: 'Ready', note: 'Nothing is plotting right now.' };
    }

    if (status === 'connected') {
        return { state: 'warning', text: 'Connected', note: 'The plotter is connected but may be powered off.' };
    }

    return { state: 'off', text: 'Disconnected', note: 'No plotter was found on USB.' };
}

function buildJobSubtitle(job) {
    const parts = [];

    if (job.title && job.filename) {
        parts.push(job.filename);
    }
    if (Number(job.layer) > 0) {
        parts.push(`Layer ${job.layer}`);
    }
    if (Number(job.editions) > 1) {
        parts.push(`Edition ${job.edition} of ${job.editions}`);
    }

    return parts.join(' · ');
}

function renderThumbnail(job) {
    // Between plots, forget any failure so the next plot's thumbnail gets a fresh try.
    if (!job) {
        shownThumbnailUrl = '';
        failedThumbnailUrl = '';
    }

    const thumbnailUrl = job?.thumbnail_url || '';
    const canShow = Boolean(thumbnailUrl) && thumbnailUrl !== failedThumbnailUrl;

    byId('monitor-thumb').hidden = !canShow;

    // Only touch src when it changes, otherwise the image reloads every tick.
    if (canShow && thumbnailUrl !== shownThumbnailUrl) {
        shownThumbnailUrl = thumbnailUrl;
        byId('monitor-thumb-img').src = thumbnailUrl;
    }
}

function renderPlotProgress(progress, nowMs) {
    const { job } = progress;

    byId('monitor-job').hidden = false;
    byId('monitor-title').textContent = job.title || job.filename || 'Untitled plot';
    const subtitle = buildJobSubtitle(job);
    byId('monitor-subtitle').textContent = subtitle;
    byId('monitor-subtitle').hidden = !subtitle;

    byId('monitor-elapsed').textContent = formatClock(progress.elapsedSeconds);

    const hasEstimate = progress.estimatedSeconds !== null;
    byId('monitor-countdown').hidden = !hasEstimate;
    byId('monitor-playbar').hidden = false;

    if (!hasEstimate) {
        // No estimate to count down from: show elapsed time only.
        byId('monitor-fill').style.width = '0%';
        byId('monitor-percent').textContent = '';
        byId('monitor-left').textContent = 'No estimate';
        byId('monitor-playbar').removeAttribute('aria-valuenow');
        return;
    }

    // Ceil the countdown so it only reads 00:00 once the estimate has fully elapsed.
    byId('monitor-remaining').textContent = formatClock(Math.ceil(progress.remainingSeconds));
    byId('monitor-finish').textContent = progress.isOverEstimate
        ? 'Running past the estimate'
        : `Finishes about ${new Date(nowMs + progress.remainingSeconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;

    const percent = Math.floor(progress.fraction * 100);
    byId('monitor-fill').style.width = `${progress.fraction * 100}%`;
    byId('monitor-percent').textContent = `${percent}%`;
    byId('monitor-left').textContent = `${formatClock(Math.ceil(progress.remainingSeconds))} left`;
    byId('monitor-playbar').setAttribute('aria-valuenow', String(percent));
}

function render() {
    const nowMs = Date.now();
    const progress = getPlotProgress(nowMs);
    const description = describeStatus(progress);

    byId('monitor').dataset.state = description.state;
    byId('monitor-status-text').textContent = description.text;
    byId('monitor-note').textContent = description.note;
    byId('monitor-note').hidden = !description.note;
    byId('monitor-connection').hidden = !connectionLost;

    const machineName = latestStatus?.machine;
    byId('monitor-machine').textContent = machineName && String(machineName).toLowerCase() !== 'none'
        ? machineName
        : 'Plotter';

    renderThumbnail(progress?.job);

    if (progress) {
        renderPlotProgress(progress, nowMs);
    } else {
        byId('monitor-job').hidden = true;
        byId('monitor-countdown').hidden = true;
        byId('monitor-playbar').hidden = true;
    }

    document.title = progress && progress.remainingSeconds !== null
        ? `${formatClock(Math.ceil(progress.remainingSeconds))} left · Plot Monitor`
        : 'Plot Monitor';
}

async function fetchStatus() {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
        const response = await fetch('/status.json', { cache: 'no-store', signal: controller.signal });
        if (!response.ok) {
            throw new Error(`Status request failed (${response.status})`);
        }

        latestStatus = await response.json();
        latestStatusFetchedAtMs = Date.now();
        connectionLost = false;
    } catch (error) {
        console.error('Failed to refresh plotter status:', error);
        connectionLost = true;
    } finally {
        clearTimeout(timeoutId);
    }
}

async function poll() {
    clearTimeout(pollTimeout);
    await fetchStatus();
    render();

    // A hidden tab has nothing to show; visibilitychange resumes polling.
    if (document.hidden) {
        return;
    }

    const isPlotting = latestStatus?.status === 'busy';
    pollTimeout = setTimeout(poll, isPlotting || connectionLost ? PLOTTING_POLL_MS : IDLE_POLL_MS);
}

document.addEventListener('DOMContentLoaded', () => {
    byId('monitor-thumb-img').addEventListener('error', (event) => {
        failedThumbnailUrl = event.currentTarget.getAttribute('src');
        byId('monitor-thumb').hidden = true;
    });

    // Mobile browsers throttle timers in the background, so refresh as soon as the page is visible again.
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            clearTimeout(pollTimeout);
        } else {
            poll();
        }
    });

    setInterval(render, 1000);
    render();
    poll();
});
